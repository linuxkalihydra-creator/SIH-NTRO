import pytest

from backend.correlation.flows import build_flow_index, is_nonstandard_port, service_port
from backend.correlation.network import ATTRIBUTION_THRESHOLD, build_network_correlation, origin_confidence


def _tx(txid, ts, inputs, outputs, input_amounts, output_amounts, src_ip="8.8.8.8", dst_port=8333, asn=15169, country="US", observations=None):
    record = {"timestamp": ts, "txid": txid, "src_ip": src_ip, "dst_ip": "1.1.1.1", "src_port": 40000, "dst_port": dst_port,
              "input_addresses": inputs, "output_addresses": outputs, "input_amounts": input_amounts, "output_amounts": output_amounts,
              "fee": 0.0, "asn": asn, "geo_country": country}
    if observations:
        record["observations"] = observations
    return record


def _seen(ts, src_ip, dst_ip="1.1.1.1", src_port=40000, dst_port=8333):
    return {"timestamp": ts, "src_ip": src_ip, "src_port": src_port, "dst_ip": dst_ip, "dst_port": dst_port}


RECORDS = [
    _tx("b", "2024-01-01T01:00:00+00:00", ["a"], ["c", "d"], [1.0], [0.6, 0.39], src_ip="9.9.9.9", dst_port=9050, asn=19281, country="CH"),
    _tx("a", "2024-01-01T00:00:00+00:00", ["x", "y"], ["a"], [0.5, 0.6], [1.0]),
    _tx("c", "2024-01-01T02:00:00+00:00", ["c"], ["e"], [0.6], [0.59]),
]


def test_flow_index_is_chronological_with_aligned_amounts():
    flows = build_flow_index(RECORDS)
    assert [tx.txid for tx in flows.txs] == ["a", "b", "c"]
    assert flows.txs[0].inputs == [("x", 0.5), ("y", 0.6)]
    assert flows.txs[0].total_in == pytest.approx(1.1) and flows.txs[0].input_addresses == ["x", "y"]
    assert flows.next_spend("a", flows.txs[0].ts).txid == "b"
    assert flows.next_spend("e", 0) is None
    assert flows.is_fresh_output("c", flows.txs[1]) and not flows.is_fresh_output("a", flows.txs[1])


def test_unaligned_amounts_are_split_evenly():
    flows = build_flow_index([_tx("t", "2024-01-01T00:00:00+00:00", ["p", "q"], ["r"], [2.0], [1.9])])
    assert flows.txs[0].inputs == [("p", 1.0), ("q", 1.0)]


def test_records_without_a_usable_timestamp_are_refused():
    with pytest.raises(ValueError):
        build_flow_index([_tx("t", "not a time", ["p"], ["r"], [2.0], [1.9])])


def test_service_port_ignores_the_ephemeral_side():
    assert service_port(40000, 8333) == 8333 and service_port(8333, 51000) == 8333 and service_port(40000, 9050) == 9050
    assert not is_nonstandard_port(8333, 51000) and is_nonstandard_port(40000, 9050)
    assert not is_nonstandard_port(0, 0)  # an unknown port is not evidence of anything


def test_network_evidence_is_attributed_to_spenders():
    network = build_network_correlation(build_flow_index(RECORDS))
    assert set(network.wallets) == {"x", "y", "a", "c"}  # receivers of outputs reveal nothing
    footprint = network.wallets["a"]
    assert dict(footprint.ips) == {"9.9.9.9": 1} and footprint.nonstandard_port_ratio == 1.0
    assert dict(footprint.countries) == {"CH": 1} and dict(footprint.asns) == {19281: 1}
    assert network.shared_ip_wallets("x") == 2          # y and c also broadcast from 8.8.8.8
    association = network.ip_associations("a")[0]
    assert association["ip"] == "9.9.9.9" and association["exclusivity"] == 1.0 and association["association"] == 1.0
    assert ("8.8.8.8", {"x", "y", "c"}) in network.ip_links()
    assert network.ips["8.8.8.8"].originated == [0, 2] and network.ips["8.8.8.8"].relay_ratio == 0.0


def test_excluded_transactions_are_not_attributed_to_their_inputs():
    network = build_network_correlation(build_flow_index(RECORDS), exclude_txids={"a"})
    assert set(network.wallets) == {"a", "c"}  # the CoinJoin-like transaction "a" was broadcast by someone else
    assert network.origins[0].attributed and network.ips["8.8.8.8"].originated == [0, 2]


def test_first_seen_relay_is_the_origin_and_observations_are_ordered():
    record = _tx("t", "2024-01-01T00:00:03+00:00", ["a"], ["b"], [1.0], [0.99], src_ip="6.6.6.6", observations=[
        _seen("2024-01-01T00:00:07.500+00:00", "7.7.7.7"), _seen("2024-01-01T00:00:03+00:00", "6.6.6.6"), _seen("2024-01-01T00:00:03.200+00:00", "6.6.6.6", dst_ip="2.2.2.2"),
    ])
    flows = build_flow_index([record])
    tx = flows.txs[0]
    assert [observation.src_ip for observation in tx.observations] == ["6.6.6.6", "6.6.6.6", "7.7.7.7"]
    assert tx.relay_count == 3 and tx.propagation_seconds == pytest.approx(4.5)
    origin = build_network_correlation(flows).origins[0]
    assert origin.ip == "6.6.6.6" and origin.attributed and origin.lead_seconds == pytest.approx(4.5)  # the gap to the next *different* relay
    assert origin.confidence > origin_confidence(1, None, 0.0)


def test_origin_confidence_rewards_a_clear_lead_and_discounts_relay_nodes():
    single = origin_confidence(1, None, 0.0)
    assert origin_confidence(3, 0.05, 0.0) < origin_confidence(3, 2.0, 0.0) < origin_confidence(3, 30.0, 0.0) <= 0.95
    assert single >= ATTRIBUTION_THRESHOLD
    assert origin_confidence(3, 30.0, 0.9) < ATTRIBUTION_THRESHOLD < origin_confidence(3, 30.0, 0.1)


def test_a_relay_node_seen_first_is_not_mistaken_for_the_creator():
    records = []
    for index in range(6):  # 9.9.9.9 creates nothing here: it only ever passes on what 5.5.5.x announced
        records.append(_tx(f"t{index}", f"2024-01-01T00:0{index}:00+00:00", [f"in{index}"], [f"out{index}"], [1.0], [0.99], src_ip=f"5.5.5.{index}", observations=[
            _seen(f"2024-01-01T00:0{index}:00+00:00", f"5.5.5.{index}"), _seen(f"2024-01-01T00:0{index}:03+00:00", "9.9.9.9")]))
    # the creator of this one was not captured, so the relay is its first sighting
    records.append(_tx("missed", "2024-01-01T00:30:00+00:00", ["hidden"], ["out"], [1.0], [0.99], src_ip="9.9.9.9"))
    network = build_network_correlation(build_flow_index(records))
    missed = network.origins[-1]
    assert missed.ip == "9.9.9.9" and not missed.attributed and missed.confidence < 0.2
    assert network.ips["9.9.9.9"].relay_ratio == pytest.approx(6 / 7)
    assert "hidden" not in network.wallets and "9.9.9.9" not in network.ip_wallets
    assert all(origin.attributed for origin in network.origins[:-1])
