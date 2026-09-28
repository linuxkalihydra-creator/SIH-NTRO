import pandas as pd

from backend.ml.features import build_wallet_feature_frame


def test_feature_frame_has_wallet_rows_and_numeric_columns():
    records = [{
        "timestamp": "2024-01-01T00:00:00+00:00",
        "src_ip": "203.0.113.10",
        "dst_ip": "198.51.100.10",
        "src_port": 8333,
        "dst_port": 8333,
        "txid": "tx_1",
        "input_addresses": ["wallet_a"],
        "output_addresses": ["wallet_b"],
        "input_amounts": [1.0],
        "output_amounts": [0.95],
        "fee": 0.05,
        "script_type": "P2WPKH",
        "geo_country": "US",
        "asn": 64512,
        "behavior_type": "NORMAL",
    }, {
        "timestamp": "2024-01-01T00:05:00+00:00",
        "src_ip": "203.0.113.11",
        "dst_ip": "198.51.100.11",
        "src_port": 8333,
        "dst_port": 8333,
        "txid": "tx_2",
        "input_addresses": ["wallet_b"],
        "output_addresses": ["wallet_c"],
        "input_amounts": [0.95],
        "output_amounts": [0.9],
        "fee": 0.05,
        "script_type": "P2WPKH",
        "geo_country": "US",
        "asn": 64512,
        "behavior_type": "NORMAL",
    }]
    frame = build_wallet_feature_frame(records)
    assert isinstance(frame, pd.DataFrame)
    assert not frame.empty
    assert "wallet_id" in frame.columns
    assert "transaction_count" in frame.columns
    assert "total_received" in frame.columns


def _record(txid, timestamp, inputs, outputs, input_amounts, output_amounts):
    return {
        "timestamp": timestamp,
        "src_ip": "203.0.113.10",
        "dst_ip": "198.51.100.10",
        "txid": txid,
        "input_addresses": inputs,
        "output_addresses": outputs,
        "input_amounts": input_amounts,
        "output_amounts": output_amounts,
        "geo_country": "US",
        "asn": 64512,
    }


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
