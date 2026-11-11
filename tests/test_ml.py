from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from backend.correlation.flows import build_flow_index
from backend.correlation.network import build_network_correlation
from backend.ml.anomaly import DETECTORS, AnomalyEnsemble
from backend.ml.entities import (LINK_CHANGE_ELIMINATION, LINK_CHANGE_ONLY_NEW, LINK_CHANGE_SCRIPT, LINK_STRUCTURE, change_output, cluster_entities,
                                 common_input_ownership, deepwalk_embeddings, detect_communities, entity_graph, link_actors)
from backend.ml.patterns import chain_speed_factor, detect_coinjoins, detect_layering, detect_peeling_chains, detect_rapid_passthrough
from backend.ml.propagation import propagate_exposure, propagate_network_association, propagate_proceeds, propagate_risk
from backend.ml.scoring import ANOMALY_ALONE, WALLET_WEIGHTS, confidence, fuse, group_inherit, level_for

START = datetime(2024, 1, 1, tzinfo=timezone.utc)
# Syntactically valid-looking addresses of two script types (prefix decides the type).
SEGWIT = ["bc1q" + f"{index:038d}" for index in range(40)]
LEGACY = ["1" + f"{index:033d}" for index in range(40)]


def tx(txid, minutes, inputs, outputs, dst_port=8333, src_ip="8.8.8.8"):
    """inputs/outputs: lists of (address, amount)."""
    return {
        "timestamp": (START + timedelta(minutes=minutes)).isoformat(), "txid": txid, "src_ip": src_ip, "dst_ip": "1.1.1.1",
        "src_port": 40000, "dst_port": dst_port,
        "input_addresses": [a for a, _ in inputs], "input_amounts": [v for _, v in inputs],
        "output_addresses": [a for a, _ in outputs], "output_amounts": [v for _, v in outputs],
        "fee": round(sum(v for _, v in inputs) - sum(v for _, v in outputs), 8),
    }


def peeling_chain(hops=8, start=10.0, gap=30):
    records, balance, current = [], start, "hop_0"
    records.append(tx("fund", 0, [("funder", start + 0.01)], [(current, start)]))
    for index in range(hops):
        peel = round(balance * 0.08, 8)
        remainder = round(balance - peel - 0.0001, 8)
        records.append(tx(f"peel_{index}", gap * (index + 1), [(current, balance)], [(f"hop_{index + 1}", remainder), (f"recipient_{index}", peel)]))
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
    # The last remainder is where the bulk ended up, not a proven hop, and is not merged into the owner.
    assert chain.roles["hop_8"] == "chain_exit" and chain.owner_groups == [[f"hop_{index}" for index in range(8)]]


def test_short_payment_sequences_are_not_peeling_chains():
    assert detect_peeling_chains(build_flow_index(peeling_chain(hops=3)), set()) == []


def test_a_slow_chain_is_weaker_than_a_fast_one():
    fast = detect_peeling_chains(build_flow_index(peeling_chain(hops=6, gap=20)), set())[0]
    slow = detect_peeling_chains(build_flow_index(peeling_chain(hops=6, gap=60 * 40)), set())[0]  # a payment every 40 hours
    assert slow.strength < 0.5 * fast.strength
    assert chain_speed_factor(30) == 1.0 and chain_speed_factor(72 * 60) == pytest.approx(0.3)


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


def layering_records():
    records = [tx("split", 0, [("source", 4.0)], [(f"mid_{i}", 0.99) for i in range(4)])]
    for i in range(4):
        records.append(tx(f"hop_{i}", 60 + i, [(f"mid_{i}", 0.99)], [(f"mid2_{i}", 0.96), (f"left_{i}", 0.025)]))
        records.append(tx(f"join_{i}", 300 + i, [(f"mid2_{i}", 0.96)], [("sink", 0.955)]))
    records.append(tx("cash_out", 900, [("sink", 3.82)], [("deposit", 3.81)]))
    return records


def test_layering_fan_out_fan_in_is_detected():
    found = detect_layering(build_flow_index(layering_records()), set())
    assert len(found) == 1
    pattern = found[0]
    assert pattern.roles["sink"] == "layering_sink" and pattern.details["converged_branches"] == 4
    assert pattern.roles["mid_0"] == "layering_intermediate" and pattern.roles["mid2_3"] == "layering_intermediate"
    assert pattern.roles["left_0"] == "layering_change" and pattern.roles["deposit"] == "layering_exit"
    assert "cash_out" in pattern.txids and pattern.details["value_reaching_sink"] > 0.9
    owners = set(pattern.owner_groups[0])
    assert {"source", "mid_0", "mid2_0", "sink"} <= owners and not owners & {"left_0", "deposit"}


def test_the_sink_is_where_branches_first_meet_not_where_the_funds_go_next():
    # Every branch reaches "sink" and then, together, "deposit": both are reached by four branches.
    pattern = detect_layering(build_flow_index(layering_records()), set())[0]
    assert pattern.details["sink"] == "sink"


def test_a_service_sweep_is_not_layering():
    # Three fresh deposit addresses are funded by one payout and later swept, each together with other customers' funds.
    records = [tx("payout", 0, [("payer", 3.1)], [("dep_0", 1.0), ("dep_1", 1.0), ("dep_2", 1.0)])]
    for i in range(3):
        records.append(tx(f"other_{i}", 5 + i, [(f"customer_{i}", 9.0)], [(f"other_dep_{i}", 8.9)]))
        records.append(tx(f"sweep_{i}", 60 + i, [(f"dep_{i}", 1.0), (f"other_dep_{i}", 8.9)], [("hot_wallet", 9.89)]))
    assert detect_layering(build_flow_index(records), set()) == []


def test_rapid_pass_through_needs_a_quick_near_total_forward():
    records = [
        tx("pay_mule", 0, [("boss", 1.0)], [("mule", 0.99)]),
        tx("forward", 8, [("mule", 0.99)], [("exchange_deposit", 0.975), ("mule_change", 0.0149)]),
        tx("pay_user", 0, [("employer", 1.0)], [("user", 0.99)]),
        tx("user_buys", 9, [("user", 0.99)], [("shop", 0.3), ("user_change", 0.6899)]),  # ordinary payment + change
    ]
    found = detect_rapid_passthrough(build_flow_index(records), set())
    assert [pattern.roles for pattern in found] == [{"mule": "pass_through", "exchange_deposit": "pass_through_recipient", "mule_change": "pass_through_change"}]
    assert found[0].details["forward_txids"] == ["forward"] and found[0].owner_groups == []


def test_a_sweep_of_two_deposits_is_not_a_pass_through():
    records = [
        tx("deposit_a", 0, [("customer_a", 1.0)], [("dep_a", 0.99)]),
        tx("deposit_b", 1, [("customer_b", 5.0)], [("dep_b", 4.99)]),
        tx("sweep", 10, [("dep_a", 0.99), ("dep_b", 4.99)], [("hot_wallet", 5.979)]),
    ]
    assert detect_rapid_passthrough(build_flow_index(records), set()) == []


# ── entity clustering ───────────────────────────────────────────────────────
def test_common_input_ownership_is_transitive():
    records = [tx("a", 0, [("x", 1), ("y", 1)], [("z", 1.9)]), tx("b", 10, [("y2", 1), ("x", 0.5)], [("w", 1.4)])]
    clustering = common_input_ownership(build_flow_index(records), set())
    assert clustering.entity_of["x"] == clustering.entity_of["y"] == clustering.entity_of["y2"]
    assert clustering.size("z") == 1 and clustering.evidence["y"][0] == "co-spend"


def test_change_is_the_only_new_address_among_the_outputs():
    payer, shop, change = SEGWIT[0], SEGWIT[1], SEGWIT[2]
    records = [tx("earlier", 0, [("someone", 1.0)], [(shop, 0.99)]), tx("pay", 10, [(payer, 1.0)], [(shop, 0.3), (change, 0.6999)])]
    flows = build_flow_index(records)
    assert change_output(flows, flows.txs[1]) == (change, LINK_CHANGE_ONLY_NEW)
    clustering = cluster_entities(flows, set())
    assert clustering.entity_of[payer] == clustering.entity_of[change] != clustering.entity_of[shop]
    assert clustering.evidence[change] == [LINK_CHANGE_ONLY_NEW, "pay"]
    assert common_input_ownership(flows, set()).entity_of[payer] != common_input_ownership(flows, set()).entity_of[change]


def test_change_is_the_only_output_matching_the_input_script_type():
    payer, change, payee = SEGWIT[0], SEGWIT[1], LEGACY[0]   # both outputs are new, only one matches the spender's type
    flows = build_flow_index([tx("pay", 0, [(payer, 1.0)], [(payee, 0.3), (change, 0.6999)])])
    assert change_output(flows, flows.txs[0]) == (change, LINK_CHANGE_SCRIPT)


def test_ambiguous_change_is_not_linked():
    payer = SEGWIT[0]
    both_new_same_type = build_flow_index([tx("pay", 0, [(payer, 1.0)], [(SEGWIT[1], 0.3), (SEGWIT[2], 0.6999)])])
    assert change_output(both_new_same_type, both_new_same_type.txs[0]) is None
    reuse = build_flow_index([tx("pay", 0, [(payer, 1.0)], [(SEGWIT[1], 0.3), (payer, 0.6999)])])   # the spender reuses its own address
    assert change_output(reuse, reuse.txs[0]) is None
    received_again = build_flow_index([tx("pay", 0, [(payer, 1.0)], [(LEGACY[0], 0.3), (SEGWIT[1], 0.6999)]),
                                       tx("later", 5, [("other", 1.0)], [(SEGWIT[1], 0.9)])])                # not a one-time change address
    assert change_output(received_again, received_again.txs[0]) is None
    unknown_types = build_flow_index([tx("pay", 0, [("wallet_a", 1.0)], [("wallet_b", 0.3), ("wallet_c", 0.6999)])])
    assert change_output(unknown_types, unknown_types.txs[0]) is None
    assert cluster_entities(both_new_same_type, set()).size(payer) == 1


def exchange(customers=70):
    """An exchange: every customer pays into a deposit address of their own, and deposits are swept together into a hot wallet."""
    records = [tx(f"deposit_{index}", index, [(f"customer_{index}", 1.0)], [(f"dep_{index}", 0.999)]) for index in range(customers)]
    for batch in range(0, customers, 10):
        swept = [(f"dep_{index}", 0.999) for index in range(batch, min(batch + 10, customers))]
        swept.append(("dep_0", 0.0001) if batch else ("hot", 0.0001))   # every sweep shares an input with the first: one cluster
        records.append(tx(f"sweep_{batch}", 500 + batch, swept, [("hot", round(sum(value for _, value in swept) - 0.0005, 8))]))
    return records


def test_change_is_the_new_output_that_was_not_a_payment_to_a_service():
    payer, deposit, change = SEGWIT[0], SEGWIT[1], SEGWIT[2]            # both outputs are new, one-time and of the spender's type
    pay = tx("pay", 300, [(payer, 2.0)], [(deposit, 1.5), (change, 0.4999)])
    sweep = tx("sweep_new", 900, [(deposit, 1.5), ("dep_0", 0.0001)], [("hot", 1.5)])   # the exchange later sweeps the deposit
    flows = build_flow_index(exchange() + [pay, sweep])
    assert change_output(flows, flows.txs[flows.by_txid["pay"]]) is None
    clustering = cluster_entities(flows, set())
    assert clustering.entity_of[change] == clustering.entity_of[payer] != clustering.entity_of[deposit]
    assert clustering.entity_of[deposit] == clustering.entity_of["dep_5"]
    assert clustering.evidence[change] == [LINK_CHANGE_ELIMINATION, "pay"] and clustering.link_counts[LINK_CHANGE_ELIMINATION] == 1
    assert cluster_entities(flows, set(), use_elimination=False).size(payer) == 1

    # A payee that is not a service proves nothing: both new outputs could be the change.
    small = build_flow_index([pay, tx("sweep_new", 900, [(deposit, 1.5), ("friend", 0.5)], [("friend2", 1.99)])])
    assert cluster_entities(small, set()).size(payer) == 1
    # Three new outputs with only one paid to the service: two candidates remain, nothing is linked.
    three = tx("pay", 300, [(payer, 2.0)], [(deposit, 1.0), (change, 0.4999), (SEGWIT[3], 0.5)])
    assert cluster_entities(build_flow_index(exchange() + [three, sweep]), set()).size(payer) == 1
    # A remaining output that is later spent together with someone else's addresses is theirs, not the change.
    spent_elsewhere = tx("other_wallet", 950, [(change, 0.4999), ("stranger", 1.0)], [("stranger2", 1.49)])
    taken = cluster_entities(build_flow_index(exchange() + [pay, sweep, spent_elsewhere]), set())
    assert taken.entity_of[change] == taken.entity_of["stranger"] != taken.entity_of[payer]


def test_a_laundering_structure_makes_its_wallets_one_entity():
    flows = build_flow_index(peeling_chain(hops=8))
    chain = detect_peeling_chains(flows, set())[0]
    clustering = cluster_entities(flows, set(), structures=[(chain.pattern_id, group) for group in chain.owner_groups])
    assert len({clustering.entity_of[f"hop_{index}"] for index in range(8)}) == 1
    assert clustering.entity_of["hop_8"] != clustering.entity_of["hop_0"]          # the exit is not assumed to be the owner
    assert clustering.entity_of["recipient_2"] != clustering.entity_of["hop_0"]
    assert clustering.evidence["hop_5"] == [LINK_STRUCTURE, chain.pattern_id] and clustering.link_counts[LINK_STRUCTURE] == 7


def test_actors_need_a_shared_ip_and_a_direct_payment():
    records = [
        tx("a_pays_b", 0, [("a", 1.0)], [("b", 0.99)], src_ip="5.5.5.5"),
        tx("b_spends", 10, [("b", 0.99)], [("shop", 0.98)], src_ip="5.5.5.5"),        # same IP as a, and a paid b: one actor
        tx("c_spends", 20, [("c", 1.0)], [("shop2", 0.99)], src_ip="5.5.5.5"),        # same IP but no payment with a or b
        tx("a_pays_d", 30, [("a2", 1.0)], [("d", 0.99)], src_ip="5.5.5.5"),
        tx("d_spends", 40, [("d", 0.99)], [("shop3", 0.98)], src_ip="7.7.7.7"),       # paid by the actor but broadcasts elsewhere
    ]
    flows = build_flow_index(records)
    network = build_network_correlation(flows)
    clustering = cluster_entities(flows, set())
    actors = link_actors(flows, network.origins, clustering.entity_of)
    actor = lambda address: actors.actor_of[clustering.entity_of[address]]  # noqa: E731
    assert actor("a") == actor("b")
    assert len({actor("a"), actor("c"), actor("d"), actor("shop")}) == 4
    assert actors.evidence[clustering.entity_of["b"]] == {"kind": "payment", "ip": "5.5.5.5", "txid": "a_pays_b", "with": clustering.entity_of["a"]}
    assert actors.link_counts == {"payment": 1}
    # Services and widely shared IPs carry no ownership information.
    as_service = link_actors(flows, network.origins, clustering.entity_of, service={clustering.entity_of["a"]: 0.9})
    assert as_service.actor_of[clustering.entity_of["a"]] != as_service.actor_of[clustering.entity_of["b"]]
    crowded = link_actors(flows, network.origins, clustering.entity_of, max_entities_per_ip=2)
    assert crowded.skipped_ips == ["5.5.5.5"] and crowded.actor_of[clustering.entity_of["a"]] != crowded.actor_of[clustering.entity_of["b"]]


def test_actor_groups_grow_as_an_ip_pool_is_discovered():
    records = [
        tx("t1", 0, [("a", 3.0)], [("b", 2.99)], src_ip="5.5.5.1"),
        tx("t2", 10, [("b", 2.99)], [("c", 2.98)], src_ip="5.5.5.2"),   # b has not used a's IP yet ...
        tx("t3", 20, [("c", 2.98)], [("d", 1.0), ("b", 1.97)], src_ip="5.5.5.2"),
        tx("t4", 30, [("b", 1.97)], [("e", 1.96)], src_ip="5.5.5.1"),   # ... until here, which links a-b, then b-c through 5.5.5.2
    ]
    flows = build_flow_index(records)
    clustering = cluster_entities(flows, set())
    actors = link_actors(flows, build_network_correlation(flows).origins, clustering.entity_of)
    assert len({actors.actor_of[clustering.entity_of[address]] for address in ("a", "b", "c")}) == 1


def test_taint_fades_faster_once_it_is_spent_by_uninvolved_wallets():
    records = [
        tx("s1", 0, [("seed", 10.0)], [("hop", 10.0)]),
        tx("s2", 10, [("hop", 10.0)], [("shop", 10.0)]),          # the launderer pays a shop
        tx("s3", 20, [("shop", 10.0)], [("supplier", 10.0)]),     # the shop pays its supplier
    ]
    flows = build_flow_index(records)
    plain = propagate_risk(flows, ["seed"])
    careful = propagate_risk(flows, ["seed"], launderers={"hop"}, spent_decay=0.5)
    assert careful.wallets["hop"].score == pytest.approx(0.9) and careful.wallets["shop"].score == pytest.approx(0.81)
    assert plain.wallets["supplier"].score == pytest.approx(0.729)
    assert careful.wallets["supplier"].score == pytest.approx(0.405)   # two steps from the crime
    proceeds, _ = propagate_proceeds(flows, {"hop": 1.0}, launderers={"hop"}, spent_decay=0.5)
    assert proceeds["shop"].score == pytest.approx(0.9) and proceeds["supplier"].score == pytest.approx(0.45)


def test_wallets_funding_the_same_deposit_address_are_one_actor_with_a_second_clue():
    records = exchange() + [
        tx("a_deposits", 300, [("a", 1.0)], [("dep_3", 0.99)], src_ip="5.5.5.5"),
        tx("b_deposits", 310, [("b", 1.0)], [("dep_3", 0.99)], src_ip="5.5.5.5"),     # same deposit address and same IP as a
        tx("c_deposits", 320, [("c", 1.0)], [("dep_3", 0.99)], src_ip="7.7.7.7"),     # same deposit address only
        tx("d_pays_e", 330, [("d", 2.0)], [("e", 1.99)], src_ip="9.9.9.1"),
        tx("d_deposits", 340, [("d2", 1.0)], [("dep_7", 0.99)], src_ip="9.9.9.1"),
        tx("link_d", 341, [("d", 0.5), ("d2", 0.5)], [("d3", 0.99)], src_ip="9.9.9.1"),
        tx("e_deposits", 350, [("e", 1.99)], [("dep_7", 1.98)], src_ip="9.9.9.2"),    # paid by d and funds d's deposit address: no IP needed
    ]
    flows = build_flow_index(records)
    network = build_network_correlation(flows)
    clustering = cluster_entities(flows, set())
    entity = clustering.entity_of
    counterparties = {entity["dep_3"]: 1.0}                                         # the exchange cluster is a service
    assert entity["dep_3"] == entity["dep_7"]
    actors = link_actors(flows, network.origins, entity, service=counterparties)
    actor = lambda address: actors.actor_of[entity[address]]  # noqa: E731
    assert actor("a") == actor("b") != actor("c")
    assert actors.evidence[entity["b"]] == {"kind": "deposit", "ip": "5.5.5.5", "txid": "a_deposits", "address": "dep_3", "with": entity["a"]}
    assert actor("d") == actor("e") and actor("d") != actor("a")
    assert actors.evidence[entity["e"]] == {"kind": "payment+deposit", "ip": "", "txid": "d_pays_e", "address": "dep_7", "with": entity["d"]}
    assert actors.link_counts == {"payment+deposit": 1, "deposit": 1}
    # An address that many entities pay is a public payment address, not one customer's account.
    assert link_actors(flows, network.origins, entity, service=counterparties, max_depositors=2).actor_of[entity["a"]] != \
        link_actors(flows, network.origins, entity, service=counterparties, max_depositors=2).actor_of[entity["b"]]
    # Without knowing that the payee is a service, paying the same address is no tie at all.
    plain = link_actors(flows, network.origins, entity)
    assert plain.actor_of[entity["a"]] != plain.actor_of[entity["b"]]


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


def test_taint_does_not_decay_inside_one_entity_and_is_discounted_through_services():
    flows = build_flow_index(peeling_chain(hops=8))
    chain = detect_peeling_chains(flows, set())[0]
    per_hop = propagate_risk(flows, ["funder"])
    assert per_hop.wallets["hop_6"].score == pytest.approx(0.9 ** 7)
    clustering = cluster_entities(flows, set(), structures=[(chain.pattern_id, group) for group in chain.owner_groups])
    owned = propagate_risk(flows, ["funder"], clustering)
    assert owned.wallets["hop_6"].score == pytest.approx(0.9)            # one decay leaving the seed's entity, none along the owner's own chain
    assert owned.wallets["recipient_6"].score == pytest.approx(0.81)     # and one more to a different party
    through_service = propagate_risk(flows, ["funder"], service={"hop_1": 1.0})
    assert "hop_2" not in through_service.wallets                        # a service's payouts are not traced as the criminal's money


def test_proceeds_of_a_structure_are_followed_without_any_seed():
    records = [
        tx("pay_mule", 0, [("chain_hop", 2.0)], [("mule", 1.0), ("next_hop", 0.99)]),
        tx("forward", 8, [("mule", 1.0)], [("deposit", 0.98)]),
        tx("clean_deposit", 9, [("customer", 1.0)], [("deposit", 0.98)]),
    ]
    flows = build_flow_index(records)
    wallets, transactions = propagate_proceeds(flows, {"chain_hop": 1.0, "mule": 0.8})
    assert "chain_hop" not in wallets and "mule" not in wallets                  # sources are scored by their pattern, not by this
    assert wallets["next_hop"].score == pytest.approx(0.9) and wallets["next_hop"].reason == "downstream"
    assert wallets["deposit"].score == pytest.approx(0.8 * 0.9 * 0.5)            # half of what it received came from the mule
    assert transactions["forward"] == pytest.approx(0.8) and "clean_deposit" not in transactions


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
    assert "vault" not in propagate_exposure(build_flow_index(records), {"chain_start": 1.0}, service={"vault": 1.0})


def test_network_association_marks_wallets_on_dirty_infrastructure_only():
    records = [tx(f"dirty_{index}", index, [(f"flagged_{index}", 1.0)], [(f"out_{index}", 0.99)], src_ip="6.6.6.6") for index in range(4)]
    records.append(tx("quiet", 10, [("unflagged", 1.0)], [("x", 0.99)], src_ip="6.6.6.6"))         # shares the operator's IP
    records += [tx(f"crowd_{index}", 20 + index, [(f"bystander_{index}", 1.0)], [(f"y_{index}", 0.99)], src_ip="9.9.9.9") for index in range(30)]
    records.append(tx("one_bad_apple", 60, [("flagged_vpn_user", 1.0)], [("z", 0.99)], src_ip="9.9.9.9"))   # one flagged wallet on a busy exit
    network = build_network_correlation(build_flow_index(records))
    direct = {**{f"flagged_{index}": 1.0 for index in range(4)}, "flagged_vpn_user": 1.0}
    result = propagate_network_association(network, direct)
    assert result.signal["unflagged"] == pytest.approx(4 / 5)                    # 4 flagged of the 4 other broadcasts, smoothed by one
    assert result.evidence["unflagged"]["ip"] == "6.6.6.6" and result.evidence["unflagged"]["flagged_wallets_on_ip"] == 4
    assert result.signal["bystander_3"] < 0.1                                    # a shared exit stays clean
    assert result.ip_dirtiness["6.6.6.6"] > 0.7 > 0.2 > result.ip_dirtiness["9.9.9.9"]
    assert result.signal["flagged_0"] < 1.0                                      # a wallet's own traffic never counts for itself


# ── fusion ──────────────────────────────────────────────────────────────────
def test_fusion_contributions_are_exact_shapley_values():
    signals = np.array([[1.0, 0.0, 0.0, 0.0], [0.8, 0.9, 0.5, 0.2], [0.0, 0.0, 0.0, 0.0]])
    weights = np.array([0.55, 0.7, 0.9, 0.35])
    risk, contributions = fuse(signals, weights)
    assert risk[0] == pytest.approx(55.0) and contributions[0, 0] == pytest.approx(55.0)
    assert np.allclose(contributions.sum(axis=1), risk)
    assert risk[2] == 0 and (risk <= 100).all()
    assert [level_for(value) for value in (85, 65, 40, 5)] == ["CRITICAL", "HIGH", "MEDIUM", "LOW"]


def test_anomaly_counts_in_full_only_when_corroborated():
    alone = np.array([[1.0, 0.0, 0.0, 0.0]])
    backed = np.array([[1.0, 0.9, 0.0, 0.0]])
    risk_alone, contributions_alone = fuse(alone, WALLET_WEIGHTS, ANOMALY_ALONE)
    risk_backed, contributions_backed = fuse(backed, WALLET_WEIGHTS, ANOMALY_ALONE)
    assert risk_alone[0] == pytest.approx(100 * ANOMALY_ALONE) and level_for(risk_alone[0]) == "LOW"   # odd behaviour alone is a weak lead
    assert fuse(alone, WALLET_WEIGHTS)[0][0] == pytest.approx(100 * WALLET_WEIGHTS[0])                 # without gating it would be MEDIUM
    always_low = WALLET_WEIGHTS.copy()
    always_low[0] = ANOMALY_ALONE
    pattern_only = fuse(np.array([[0.0, 0.9, 0.0, 0.0]]), WALLET_WEIGHTS, ANOMALY_ALONE)[0][0]
    # Corroborated by a strong pattern, the anomaly adds far more than it would at its stand-alone weight ...
    assert risk_backed[0] - pattern_only > 2 * (fuse(backed, always_low)[0][0] - pattern_only)
    # ... but never more than its full weight allows.
    assert risk_backed[0] <= fuse(backed, WALLET_WEIGHTS)[0][0]
    assert np.allclose(contributions_backed.sum(axis=1), risk_backed) and np.allclose(contributions_alone.sum(axis=1), risk_alone)
    assert (contributions_backed >= 0).all() and contributions_backed[0, 2] == 0 and contributions_backed[0, 3] == 0


def test_group_evidence_needs_coverage():
    codes = np.array([0, 0, 0, 0] + [1] * 40)
    values = np.array([0.9, 0.0, 0.0, 0.0] + [0.9] + [0.0] * 39)
    shared, source = group_inherit(values, codes)
    assert shared[1] == pytest.approx(0.9) and source[1] == 0          # a small group in which one wallet is implicated
    assert shared[10] == pytest.approx(0.9 * 0.1) and source[10] == 4  # one deposit does not condemn a 40-address service
    assert group_inherit(np.array([]), np.array([], dtype=int))[0].size == 0


def test_confidence_rewards_corroboration():
    weighted = np.array([[0.5, 0.0, 0.0, 0.0], [0.5, 0.6, 0.7, 0.0]])
    single, corroborated = confidence(weighted, np.array([1 / 3, 1.0]), np.array([False, False]))
    assert single < corroborated <= 0.99
    assert confidence(weighted[:1], np.array([0.0]), np.array([True]))[0] == 1.0
    own, inherited = confidence(weighted[[1, 1]], np.array([0.0, 0.0]), np.array([False, False]), np.array([False, True]))
    assert inherited < own
