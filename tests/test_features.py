import pandas as pd
import pytest

from backend.correlation.flows import build_flow_index
from backend.correlation.network import build_network_correlation
from backend.ml.entities import cluster_entities
from backend.ml.features import (ENTITY_MODEL_FEATURES, TRANSACTION_MODEL_FEATURES, WALLET_MODEL_FEATURES, build_entity_features, build_transaction_features,
                                 build_wallet_feature_frame, build_wallet_features, service_score)


def _record(txid, timestamp, inputs, outputs, input_amounts, output_amounts, src_ip="203.0.113.10", dst_port=8333, asn=64512, country="US"):
    return {
        "timestamp": timestamp,
        "src_ip": src_ip,
        "dst_ip": "198.51.100.10",
        "dst_port": dst_port,
        "txid": txid,
        "input_addresses": inputs,
        "output_addresses": outputs,
        "input_amounts": input_amounts,
        "output_amounts": output_amounts,
        "geo_country": country,
        "asn": asn,
    }


def test_feature_frame_has_wallet_rows_and_numeric_columns():
    frame = build_wallet_feature_frame([
        _record("tx_1", "2024-01-01T00:00:00+00:00", ["wallet_a"], ["wallet_b"], [1.0], [0.95]),
        _record("tx_2", "2024-01-01T00:05:00+00:00", ["wallet_b"], ["wallet_c"], [0.95], [0.9]),
    ])
    assert isinstance(frame, pd.DataFrame) and not frame.empty
    assert {"wallet_id", "transaction_count", "total_received", *WALLET_MODEL_FEATURES} <= set(frame.columns)


def test_counterparties_are_counted_in_multi_wallet_transactions():
    frame = build_wallet_feature_frame([
        _record("tx_1", "2024-01-01T00:00:00+00:00", ["wallet_a", "wallet_b"], ["wallet_c"], [1.0, 2.0], [2.9]),
    ]).set_index("wallet_id")
    assert frame.loc["wallet_c", "unique_counterparties"] == 2
    assert frame.loc["wallet_a", "unique_counterparties"] == 2
    assert (frame["unique_counterparties"] > 0).all()


def test_transactions_per_hour_uses_timestamp_span():
    frame = build_wallet_feature_frame([
        _record("tx_1", "2024-01-01T00:00:00+00:00", ["wallet_a"], ["wallet_b"], [1.0], [0.9]),
        _record("tx_2", "2024-01-01T01:00:00+00:00", ["wallet_a"], ["wallet_c"], [1.0], [0.9]),
    ]).set_index("wallet_id")
    assert abs(frame.loc["wallet_a", "transactions_per_hour"] - 1.0) < 1e-6
    assert abs(frame.loc["wallet_a", "transactions_per_day"] - 24.0) < 1e-6
    assert frame.loc["wallet_b", "transactions_per_hour"] == 0.0


def test_inputs_are_outgoing_and_outputs_are_incoming():
    frame = build_wallet_feature_frame([
        _record("tx_1", "2024-01-01T00:00:00+00:00", ["sender"], ["receiver"], [1.0], [0.9]),
    ]).set_index("wallet_id")
    sender, receiver = frame.loc["sender"], frame.loc["receiver"]
    assert (sender["outgoing_transaction_count"], sender["incoming_transaction_count"]) == (1, 0)
    assert (sender["graph_out_degree"], sender["graph_in_degree"]) == (1, 0)
    assert sender["total_sent"] == 1.0 and sender["total_received"] == 0.0
    assert (receiver["incoming_transaction_count"], receiver["outgoing_transaction_count"]) == (1, 0)
    assert (receiver["graph_in_degree"], receiver["graph_out_degree"]) == (1, 0)
    assert receiver["total_received"] == 0.9 and receiver["total_sent"] == 0.0


def test_entity_features_describe_the_owner_not_the_address():
    records = [
        _record("fund", "2024-01-01T00:00:00+00:00", ["payer"], ["a1", "a2"], [3.0], [1.0, 1.99]),
        # a1 and a2 are spent together: one entity, broadcasting from two networks, once on a non-standard port
        _record("spend_1", "2024-01-01T01:00:00+00:00", ["a1", "a2"], ["shop_1", "a3"], [1.0, 1.99], [0.5, 2.48], src_ip="5.5.5.5", asn=64600, country="DE"),
        _record("internal", "2024-01-01T02:00:00+00:00", ["a1"], ["a2"], [0.0], [0.0]),
        _record("spend_2", "2024-01-01T03:00:00+00:00", ["a2", "a3"], ["shop_2"], [0.0, 2.48], [2.47], src_ip="6.6.6.6", dst_port=9050, asn=64700, country="NL"),
    ]
    flows = build_flow_index(records)
    network = build_network_correlation(flows)
    clustering = cluster_entities(flows, set())
    wallets = build_wallet_features(flows, network).set_index("wallet_id", drop=False)
    entities = build_entity_features(flows, network, clustering.entity_of, clustering.members, wallets).set_index("entity_id")
    owner = entities.loc[clustering.entity_of["a1"]]
    assert set(ENTITY_MODEL_FEATURES) <= set(entities.columns)
    assert owner["address_count"] == 3 and clustering.entity_of["a3"] == clustering.entity_of["a1"]
    assert owner["received_value"] == pytest.approx(2.99)                 # what others paid it; moving funds between its own addresses is not income
    assert owner["sent_value"] == pytest.approx(0.5 + 2.47)
    assert owner["spend_count"] == 3 and owner["counterparty_entities"] == 3   # payer, shop_1, shop_2
    assert (owner["relay_ips"], owner["relay_asns"], owner["relay_countries"]) == (3, 3, 3)
    # One odd port in three broadcasts is shrunk towards the dataset average instead of being read as "a third of the time".
    assert 0 < owner["nonstandard_port_share"] < 1 / 3
    assert entities.loc[clustering.entity_of["shop_1"], "spend_count"] == 0


def test_service_score_separates_hubs_from_ordinary_wallets():
    assert service_score(5) == 0.0 and service_score(20) == 0.0
    assert 0.3 < service_score(60) < 0.7 and service_score(200) == 1.0 and service_score(5000) == 1.0


def test_transaction_features_include_how_the_transaction_travelled():
    record = _record("t", "2024-01-01T00:00:00+00:00", ["a"], ["b", "c"], [1.0], [0.5, 0.49])
    record["observations"] = [
        {"timestamp": "2024-01-01T00:00:00+00:00", "src_ip": "203.0.113.10", "src_port": 40000, "dst_ip": "198.51.100.10", "dst_port": 8333},
        {"timestamp": "2024-01-01T00:00:02.500+00:00", "src_ip": "198.51.100.10", "src_port": 8333, "dst_ip": "198.51.100.99", "dst_port": 51000},
    ]
    flows = build_flow_index([record])
    frame = build_transaction_features(flows, build_network_correlation(flows)).set_index("txid")
    row = frame.loc["t"]
    assert set(TRANSACTION_MODEL_FEATURES) <= set(frame.columns)
    assert row["relay_count"] == 2 and row["propagation_seconds"] == 2.5 and row["origin_confidence"] > 0.6
    assert row["fresh_output_ratio"] == 1.0 and row["nonstandard_port"] == 0
