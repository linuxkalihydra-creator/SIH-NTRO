from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from backend.correlation.flows import build_flow_index
from backend.ml.anomaly import DETECTORS, AnomalyEnsemble
from backend.ml.entities import common_input_ownership, deepwalk_embeddings, detect_communities, entity_graph
from backend.ml.patterns import detect_coinjoins, detect_layering, detect_peeling_chains, detect_rapid_passthrough
from backend.ml.propagation import propagate_exposure, propagate_risk
from backend.ml.scoring import confidence, fuse, level_for

START = datetime(2024, 1, 1, tzinfo=timezone.utc)


def tx(txid, minutes, inputs, outputs, dst_port=8333, src_ip="8.8.8.8"):
    """inputs/outputs: lists of (address, amount)."""
    return {
        "timestamp": (START + timedelta(minutes=minutes)).isoformat(), "txid": txid, "src_ip": src_ip, "dst_ip": "1.1.1.1",
        "src_port": 40000, "dst_port": dst_port,
        "input_addresses": [a for a, _ in inputs], "input_amounts": [v for _, v in inputs],
        "output_addresses": [a for a, _ in outputs], "output_amounts": [v for _, v in outputs],
        "fee": round(sum(v for _, v in inputs) - sum(v for _, v in outputs), 8),
    }


def peeling_chain(hops=8, start=10.0):
    records, balance, current = [], start, "hop_0"
    records.append(tx("fund", 0, [("funder", start + 0.01)], [(current, start)]))
    for index in range(hops):
        peel = round(balance * 0.08, 8)
        remainder = round(balance - peel - 0.0001, 8)
        records.append(tx(f"peel_{index}", 30 * (index + 1), [(current, balance)], [(f"hop_{index + 1}", remainder), (f"recipient_{index}", peel)]))
        current, balance = f"hop_{index + 1}", remainder
    return records


def coinjoin(participants=6, denomination=0.1):
    inputs = [(f"cj_in_{i}", 0.13 + i * 0.01) for i in range(participants)]
    outputs = [(f"cj_out_{i}", denomination) for i in range(participants)]
    outputs += [(f"cj_change_{i}", round(0.13 + i * 0.01 - denomination - 0.0002, 8)) for i in range(participants)]
    return tx("coinjoin", 5, inputs, outputs)


# ── patterns ────────────────────────────────────────────────────────────────
def test_peeling_chain_is_detected_with_roles():
    flows = build_flow_index(peeling_chain(hops=8))
    chains = detect_peeling_chains(flows, set())
    assert len(chains) == 1
    chain = chains[0]
    assert chain.details["hops"] == 8 and chain.details["recipients"] == 8
    assert chain.roles["hop_3"] == "chain_hop" and chain.roles["recipient_3"] == "peel_recipient"
    assert 0.6 < chain.strength <= 1.0


def test_short_payment_sequences_are_not_peeling_chains():
    assert detect_peeling_chains(build_flow_index(peeling_chain(hops=3)), set()) == []


def test_coinjoin_is_detected_and_excluded_from_common_input_ownership():
    records = [coinjoin(), tx("spend", 60, [("cj_out_0", 0.1)], [("shop", 0.0999)])]
    flows = build_flow_index(records)
    found = detect_coinjoins(flows)
    assert len(found) == 1 and found[0].details["equal_outputs"] == 6
    naive = common_input_ownership(flows, set())
    careful = common_input_ownership(flows, {found[0].txids[0]})
    assert naive.size("cj_in_0") == 6          # the heuristic's classic failure mode
    assert careful.size("cj_in_0") == 1        # CoinJoin inputs are different owners
    assert careful.excluded_coinjoins == 1


def test_batch_payouts_are_not_coinjoins():
    outputs = [(f"customer_{i}", 0.01 * (i + 1)) for i in range(8)]
    flows = build_flow_index([tx("batch", 0, [("hot", 5.0)], outputs + [("hot", 4.6)])])
    assert detect_coinjoins(flows) == []


def test_layering_fan_out_fan_in_is_detected():
    records = [tx("split", 0, [("source", 4.0)], [(f"mid_{i}", 0.99) for i in range(4)])]
    for i in range(4):
        records.append(tx(f"hop_{i}", 60 + i, [(f"mid_{i}", 0.99)], [(f"mid2_{i}", 0.985)]))
        records.append(tx(f"join_{i}", 300 + i, [(f"mid2_{i}", 0.985)], [("sink", 0.98)]))
    found = detect_layering(build_flow_index(records), set())
    assert len(found) == 1
    assert found[0].roles["sink"] == "layering_sink" and found[0].details["converged_branches"] == 4


def test_rapid_pass_through_needs_a_quick_near_total_forward():
    records = [
        tx("pay_mule", 0, [("boss", 1.0)], [("mule", 0.99)]),
        tx("forward", 8, [("mule", 0.99)], [("exchange_deposit", 0.975), ("mule_change", 0.0149)]),
        tx("pay_user", 0, [("employer", 1.0)], [("user", 0.99)]),
        tx("user_buys", 9, [("user", 0.99)], [("shop", 0.3), ("user_change", 0.6899)]),  # ordinary payment + change
    ]
    found = detect_rapid_passthrough(build_flow_index(records), set())
    assert [pattern.roles for pattern in found] == [{"mule": "pass_through", "exchange_deposit": "pass_through_recipient"}]
    assert found[0].details["forward_txids"] == ["forward"]


# ── entity clustering ───────────────────────────────────────────────────────
def test_common_input_ownership_is_transitive():
    records = [tx("a", 0, [("x", 1), ("y", 1)], [("z", 1.9)]), tx("b", 10, [("y2", 1), ("x", 0.5)], [("w", 1.4)])]
    clustering = common_input_ownership(build_flow_index(records), set())
    assert clustering.entity_of["x"] == clustering.entity_of["y"] == clustering.entity_of["y2"]
    assert clustering.size("z") == 1


def test_embeddings_and_communities_separate_disconnected_groups():
    records = []
    for group in ("a", "b"):
        for i in range(30):
            records.append(tx(f"{group}_{i}", i, [(f"{group}{i % 6}", 1.0)], [(f"{group}{(i * 7 + 3) % 6}", 0.9), (f"{group}_leaf{i}", 0.09)]))
    flows = build_flow_index(records)
    clustering = common_input_ownership(flows, set())
    entities, adjacency = entity_graph(flows, clustering)
    embedding = deepwalk_embeddings(adjacency, dimensions=8)
    assert embedding.shape == (len(entities), 8) and np.isfinite(embedding).all()
    communities = detect_communities(flows, clustering).community_of
    group_a = {communities[clustering.entity_of[f"a{i}"]] for i in range(6)}
    group_b = {communities[clustering.entity_of[f"b{i}"]] for i in range(6)}
    assert group_a.isdisjoint(group_b - {-1}) or -1 in group_a


# ── anomaly ensemble ────────────────────────────────────────────────────────
def test_anomaly_ensemble_ranks_an_injected_outlier_first_and_explains_it():
    rng = np.random.default_rng(0)
    frame = pd.DataFrame({"amount": rng.lognormal(0, 0.3, 400), "count": rng.poisson(5, 400).astype(float), "ratio": rng.uniform(0.4, 0.6, 400)})
    frame.loc[len(frame)] = [1.0, 400.0, 0.5]  # ordinary amount and ratio, extreme count
    model = AnomalyEnsemble(contamination=0.05).fit(frame)
    scores = model.score(frame)
    assert scores["ensemble_percentile"].idxmax() == len(frame) - 1
    assert scores.loc[len(frame) - 1, "agreement"] == 1.0
    assert set(f"{name} percentile" for name in DETECTORS) <= set(scores.columns)
    attributions = model.attributions(frame.iloc[[-1]])[0]
    assert attributions[0]["feature"] == "count" and attributions[0]["percentile"] == 1.0
    assert pytest.approx(sum(item["contribution"] for item in attributions), abs=0.01) == 1.0


# ── risk propagation ────────────────────────────────────────────────────────
def test_haircut_taint_is_value_weighted_time_respecting_and_traceable():
    records = [
        tx("early", 0, [("clean", 1.0)], [("mixer", 0.5)]),            # before any taint exists
        tx("dirty", 10, [("seed", 1.0)], [("mixer", 0.5), ("hop", 0.4999)]),
        tx("onward", 20, [("hop", 0.4999)], [("cashout", 0.4998)]),
        tx("backwards", 30, [("cashout", 0.4998)], [("clean", 0.4997)]),
    ]
    result = propagate_risk(build_flow_index(records), ["seed", "unknown"])
    assert result.seeds == ["seed"]
    assert result.wallets["mixer"].score == pytest.approx(0.45, abs=1e-6)  # half its value is tainted, 0.9 decay
    assert result.wallets["hop"].score == pytest.approx(0.9)
    assert result.wallets["cashout"].score == pytest.approx(0.81)
    assert result.wallets["cashout"].hops == 2
    assert [step["wallet_id"] for step in result.path("cashout")] == ["cashout", "hop", "seed"]
    assert "early" not in result.transactions


def test_seed_expands_to_co_owned_addresses():
    records = [tx("cospend", 0, [("seed", 1.0), ("sibling", 1.0)], [("out", 1.99)])]
    flows = build_flow_index(records)
    result = propagate_risk(flows, ["seed"], common_input_ownership(flows, set()))
    assert result.wallets["sibling"].reason == "co_owned" and result.co_owned == ["sibling"]


def test_upstream_exposure_stops_before_ordinary_payers():
    records = [
        tx("victim_pays", 0, [("victim", 2.0)], [("collection", 1.5), ("victim_change", 0.4999)]),
        tx("consolidate", 10, [("collection", 1.5)], [("vault", 1.4999)]),
        tx("into_chain", 20, [("vault", 1.4999)], [("chain_start", 1.4998)]),
    ]
    exposure = propagate_exposure(build_flow_index(records), {"chain_start": 1.0})
    assert exposure["vault"].hops == 1 and exposure["vault"].fraction == pytest.approx(1.0)
    assert exposure["collection"].hops == 2 and exposure["collection"].target == "chain_start"
    assert "victim" not in exposure


# ── fusion ──────────────────────────────────────────────────────────────────
def test_fusion_contributions_are_exact_shapley_values():
    signals = np.array([[1.0, 0.0, 0.0, 0.0], [0.8, 0.9, 0.5, 0.2], [0.0, 0.0, 0.0, 0.0]])
    weights = np.array([0.55, 0.7, 0.9, 0.35])
    risk, contributions = fuse(signals, weights)
    assert risk[0] == pytest.approx(55.0) and contributions[0, 0] == pytest.approx(55.0)
    assert np.allclose(contributions.sum(axis=1), risk)
    assert risk[2] == 0 and (risk <= 100).all()
    assert [level_for(value) for value in (85, 65, 40, 5)] == ["CRITICAL", "HIGH", "MEDIUM", "LOW"]


def test_confidence_rewards_corroboration():
    weighted = np.array([[0.5, 0.0, 0.0, 0.0], [0.5, 0.6, 0.7, 0.0]])
    single, corroborated = confidence(weighted, np.array([1 / 3, 1.0]), np.array([False, False]))
    assert single < corroborated <= 0.99
    assert confidence(weighted[:1], np.array([0.0]), np.array([True]))[0] == 1.0
