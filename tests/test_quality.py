"""Quality gates: detection, clustering and attribution quality on a freshly generated world.

These are deliberately looser than the figures in the write-up (which are means over many
10,000-transaction worlds): a 1,500-transaction world is noisier. They exist to catch
regressions - a change that breaks entity purity or floods the lead list fails here.
"""

import pytest

from backend.enrichment.service import enrich_dataset
from backend.pipeline.investigation import Investigation
from scripts.evaluation import evaluate


@pytest.fixture(scope="module")
def measured(small_world):
    records, _, truth = small_world
    enriched, ip_info = enrich_dataset(records)
    investigation = Investigation.build(enriched, truth["seed_wallets"], ip_info=ip_info)
    return investigation, truth, evaluate(investigation, truth)


def test_ranked_leads_are_mostly_true_leads(measured):
    _, _, metrics = measured
    wallets = metrics["wallets"]
    assert wallets["strict"]["roc_auc"] > 0.95 and wallets["investigative"]["average_precision"] > 0.8
    assert wallets["investigative"]["precision_at_25"] >= 0.95
    assert wallets["investigative"]["high_precision"] > 0.85 and wallets["investigative"]["high_recall"] > 0.5
    assert wallets["investigative"]["medium_precision"] > 0.6


def test_fused_score_beats_every_single_signal(measured):
    _, _, metrics = measured
    wallets = metrics["wallets"]
    fused = wallets["strict"]["average_precision"]
    assert all(fused > wallets[name]["average_precision"] for name in ("anomaly_only", "pattern_only", "taint_only", "network_only"))


def test_victims_are_not_flagged(measured):
    _, _, metrics = measured
    victims = next(row for row in metrics["wallets"]["by_role"] if row["role"].startswith("ransomware victims"))
    assert victims["high_or_above"] <= 0.05 and victims["medium_or_above"] <= 0.15


def test_ownership_clusters_are_pure(measured):
    _, _, metrics = measured
    entities, actors = metrics["entities"]["entities"], metrics["entities"]["actors"]
    assert entities["pair_precision"] >= 0.999 and entities["misplaced_addresses"] <= 2
    assert actors["pair_precision"] >= 0.995
    assert actors["pair_recall"] >= entities["pair_recall"]              # actor grouping only ever joins entities
    assert metrics["entities"]["entity_links"]["co-spend"] > 0


def test_origin_attribution_is_precise(measured):
    _, _, metrics = measured
    origin = metrics["origin"]
    assert origin["attributed_precision"] > 0.98 > origin["first_seen_accuracy"]     # withholding relay-first transactions pays off
    assert origin["attributed_share"] > 0.85


def test_detectors_find_the_generated_structures(measured):
    _, _, metrics = measured
    by_kind = {row["detector"]: row for row in metrics["patterns"]}
    assert by_kind["coinjoin"]["precision"] == 1.0 and by_kind["coinjoin"]["recall"] == 1.0
    assert by_kind["layering"]["precision"] >= 0.95 and by_kind["layering"]["recall"] >= 0.95
    assert by_kind["rapid_passthrough"]["recall"] >= 0.95 and by_kind["peeling_chain"]["recall"] >= 0.95


def test_an_unreported_operation_is_found_without_seeds(measured):
    investigation, truth, _ = measured
    labels = truth["wallets"]
    hidden = [address for address in investigation.wallets.index if labels[address].get("group") == "ransomware_group_2"]
    flagged = (investigation.wallets.loc[hidden, "risk_score"] >= 35).mean()
    assert len(hidden) > 10 and flagged > 0.7                                        # group 2 has no seed at all
    assert not investigation.wallets.loc[hidden, "is_seed"].any()


def test_cash_out_points_are_surfaced(measured):
    investigation, truth, _ = measured
    cashout = [address for address, item in truth["wallets"].items() if item.get("cashout_for") and address in investigation.wallets.index]
    risk = investigation.wallets.loc[cashout, "risk_score"]
    assert len(cashout) >= 5 and (risk >= 35).mean() > 0.6
    # The cash-out tag marks dirty funds arriving at a service: only service-run addresses may carry it.
    tagged = [alert for alert in investigation.wallet_alerts(400) if "cash-out" in alert["tags"]]
    assert tagged and {truth["wallets"][alert["wallet_id"]]["entity_kind"] for alert in tagged} <= {"exchange", "merchant"}
    assert any(truth["wallets"][alert["wallet_id"]].get("cashout_for") for alert in tagged)
