from backend.correlation.flows import build_flow_index
from backend.correlation.network import build_network_correlation


def _tx(txid, ts, inputs, outputs, input_amounts, output_amounts, src_ip="8.8.8.8", dst_port=8333, asn=15169, country="US"):
    return {"timestamp": ts, "txid": txid, "src_ip": src_ip, "dst_ip": "1.1.1.1", "src_port": 40000, "dst_port": dst_port,
            "input_addresses": inputs, "output_addresses": outputs, "input_amounts": input_amounts, "output_amounts": output_amounts,
            "fee": 0.0, "asn": asn, "geo_country": country}


RECORDS = [
    _tx("b", "2024-01-01T01:00:00+00:00", ["a"], ["c", "d"], [1.0], [0.6, 0.39], src_ip="9.9.9.9", dst_port=9050, asn=19281, country="CH"),
    _tx("a", "2024-01-01T00:00:00+00:00", ["x", "y"], ["a"], [0.5, 0.6], [1.0]),
    _tx("c", "2024-01-01T02:00:00+00:00", ["c"], ["e"], [0.6], [0.59]),
]


def test_flow_index_is_chronological_with_aligned_amounts():
    flows = build_flow_index(RECORDS)
    assert [tx.txid for tx in flows.txs] == ["a", "b", "c"]
    assert flows.txs[0].inputs == [("x", 0.5), ("y", 0.6)]
    assert flows.next_spend("a", flows.txs[0].ts).txid == "b"
    assert flows.next_spend("e", 0) is None
    assert flows.is_fresh_output("c", flows.txs[1]) and not flows.is_fresh_output("a", flows.txs[1])


def test_unaligned_amounts_are_split_evenly():
    flows = build_flow_index([_tx("t", "2024-01-01T00:00:00+00:00", ["p", "q"], ["r"], [2.0], [1.9])])
    assert flows.txs[0].inputs == [("p", 1.0), ("q", 1.0)]


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
