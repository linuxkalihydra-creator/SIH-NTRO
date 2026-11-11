"""Entity clustering: ownership heuristics plus graph-embedding communities.

**Who owns which addresses** (hard links, merged with union-find):

1. *Common-input ownership.* Addresses spent together as inputs of one transaction
   had to be signed by the same wallet. CoinJoin transactions are excluded because
   their inputs deliberately belong to different people, which is the main way this
   heuristic goes wrong.
2. *Change address.* The output that returns the unspent remainder belongs to the
   spender. An output is taken as change only when the evidence is unambiguous:

   * it is a brand-new address that receives exactly once in the dataset (wallets
     generate a fresh change address per spend), no input address is reused as an
     output (that reuse would itself be the change), and
   * either it is the *only* new address among the outputs - every other output
     pays an address that already existed - or it is the only output whose script
     type (P2PKH, P2SH, P2WPKH, P2TR) matches the inputs, since wallet software
     returns change in the type it spends from.

   When two outputs both qualify, nothing is linked: a wrong merge poisons every
   later conclusion about the entity, while a missed one only leaves it smaller.
3. *Change by elimination.* Some of those ties can still be settled. Once the
   first two heuristics have run, entities with very many counterparties stand
   out as services (exchanges, merchants, processors). A new output that such a
   service later sweeps together with its other addresses was a payment to it, so
   if exactly one other new one-time output remains, that one is the change. It is
   the same argument as "only new address among the outputs", with the payee
   recognised by what it does later instead of by having been seen before.

Every address records which heuristic and transaction attached it to its entity,
so the grouping can be audited.

**Who operates which entities** (:func:`link_actors`): ownership heuristics cannot
join wallets that never spend together. Two entities are put in the same *actor*
when they broadcast from the same IP address **and** money ties them: one pays the
other directly, or both pay into the same deposit address of a service (the same
exchange account). Neither clue is used alone.

**Where an entity sits in the money flow** (soft grouping):

4. *Graph embeddings.* Entities become nodes of a weighted payment graph. DeepWalk
   is computed as an explicit matrix factorisation: truncated random walks are
   sampled, window co-occurrences are counted, turned into a positive PMI matrix
   and factorised with truncated SVD (Qiu et al., "Network Embedding as Matrix
   Factorization", WSDM 2018).
5. *Communities.* HDBSCAN on the leading principal components of the embeddings
   groups entities that occupy the same region of the payment graph and leaves the
   rest unassigned (``-1``). On large graphs it is fitted on the best-connected
   entities and the remainder is assigned to the nearest community centre, which
   keeps the cost bounded.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field

import numpy as np
from scipy import sparse
from sklearn.cluster import HDBSCAN
from sklearn.decomposition import PCA, TruncatedSVD
from sklearn.preprocessing import normalize

from backend.correlation.flows import FlowIndex
from backend.ml.features import service_score

LINK_CO_SPEND = "co-spend"
LINK_CHANGE_ONLY_NEW = "change (only new address among the outputs)"
LINK_CHANGE_SCRIPT = "change (only output matching the input script type)"
LINK_CHANGE_ELIMINATION = "change (the other new outputs were payments to a service)"
LINK_STRUCTURE = "same laundering structure (peeling-chain remainder / layering legs)"
# An entity whose service score reaches this is treated as a service (exchange, merchant, processor).
SERVICE_LIMIT = 0.5
# A service address paid by more entities than this is a public payment address, not one customer's deposit address.
MAX_DEPOSITORS = 20
# Density clustering is fitted on at most this many entities; the rest join the nearest community.
MAX_DENSITY_POINTS = 12000


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, item: str) -> str:
        self.parent.setdefault(item, item)
        root = item
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[item] != root:
            self.parent[item], item = root, self.parent[item]
        return root

    def union(self, left: str, right: str) -> bool:
        a, b = self.find(left), self.find(right)
        if a == b:
            return False
        self.parent[max(a, b)] = min(a, b)
        return True


@dataclass
class EntityClustering:
    entity_of: dict[str, str]
    members: dict[str, list[str]]
    excluded_coinjoins: int
    # address -> [heuristic, txid] that attached it to its entity (absent for addresses never linked)
    evidence: dict[str, list[str]] = field(default_factory=dict)
    link_counts: dict[str, int] = field(default_factory=dict)

    def size(self, address: str) -> int:
        return len(self.members.get(self.entity_of.get(address, ""), [])) or 1


def _finish(flows: FlowIndex, forest: _UnionFind, excluded: int, evidence: dict[str, list[str]], counts: Counter) -> EntityClustering:
    groups: dict[str, list[str]] = defaultdict(list)
    for address in flows.addresses:
        groups[forest.find(address)].append(address)
    ordered = sorted(groups.values(), key=lambda items: (-len(items), flows.first_seen[items[0]]))
    entity_of, members = {}, {}
    for number, addresses in enumerate(ordered, start=1):
        entity_id = f"E{number}"
        members[entity_id] = sorted(addresses)
        for address in addresses:
            entity_of[address] = entity_id
    return EntityClustering(entity_of, members, excluded, evidence, dict(counts))


def _co_spend(flows: FlowIndex, exclude_txids: set[str], forest: _UnionFind, evidence: dict[str, list[str]], counts: Counter) -> int:
    excluded = 0
    for tx in flows.txs:
        if tx.txid in exclude_txids:
            excluded += 1
            continue
        addresses = tx.input_addresses
        for address in addresses[1:]:
            if forest.union(addresses[0], address):
                counts[LINK_CO_SPEND] += 1
            evidence.setdefault(address, [LINK_CO_SPEND, tx.txid])
        if len(addresses) > 1:
            evidence.setdefault(addresses[0], [LINK_CO_SPEND, tx.txid])
    return excluded


def change_output(flows: FlowIndex, tx) -> tuple[str, str] | None:
    """(change address, heuristic) when exactly one output is unambiguously the spender's change."""
    outputs = tx.output_addresses
    if len(outputs) < 2 or not tx.inputs or len(set(outputs)) != len(outputs):
        return None
    inputs = set(tx.input_addresses)
    if inputs.intersection(outputs):  # an input address is reused as an output: that is the change
        return None
    fresh = [address for address in outputs if flows.first_seen.get(address) == tx.index]
    one_time = [address for address in fresh if len(flows.received.get(address, ())) == 1]
    if len(fresh) == 1 and len(one_time) == 1:
        return one_time[0], LINK_CHANGE_ONLY_NEW
    kinds = {flows.address_type(address) for address in inputs}
    if len(kinds) == 1 and "" not in kinds:
        kind = next(iter(kinds))
        matching = [address for address in outputs if flows.address_type(address) == kind]
        if len(matching) == 1 and matching[0] in one_time:
            return matching[0], LINK_CHANGE_SCRIPT
    return None


def common_input_ownership(flows: FlowIndex, exclude_txids: set[str]) -> EntityClustering:
    """Entities from the common-input-ownership heuristic alone."""
    forest, evidence, counts = _UnionFind(), {}, Counter()
    excluded = _co_spend(flows, exclude_txids, forest, evidence, counts)
    return _finish(flows, forest, excluded, evidence, counts)


def _service_groups(flows: FlowIndex, owner_of: dict[str, str]) -> set[str]:
    """Groups (union-find roots) that deal with enough distinct counterparties to be a service."""
    partners: dict[str, set[str]] = defaultdict(set)
    for tx in flows.txs:
        senders = {owner_of[address] for address in tx.input_addresses}
        receivers = {owner_of[address] for address in tx.output_addresses}.difference(senders)
        for sender in senders:
            partners[sender].update(receivers)
        for receiver in receivers:
            partners[receiver].update(senders)
    return {group for group, others in partners.items() if service_score(len(others)) >= SERVICE_LIMIT}


def change_by_elimination(flows: FlowIndex, tx, owner_of: dict[str, str], group_size: dict[str, int], services: set[str]) -> str | None:
    """The one new output that is left when the other new outputs are known to be payments to a service.

    Only transactions whose new outputs are all one-time addresses are considered (any of them could be
    the change). Outputs owned by a service other than the spender are payments. If exactly one new
    output remains, and it is not already tied to addresses outside the spender's entity, it is the change.
    """
    outputs = tx.output_addresses
    if len(outputs) < 2 or not tx.inputs or len(set(outputs)) != len(outputs):
        return None
    if set(tx.input_addresses).intersection(outputs):
        return None
    fresh = [address for address in outputs if flows.first_seen.get(address) == tx.index]
    if len(fresh) < 2 or any(len(flows.received.get(address, ())) != 1 for address in fresh):
        return None
    spender = owner_of[tx.input_addresses[0]]
    left = [address for address in fresh if owner_of[address] == spender or owner_of[address] not in services]
    if len(left) != 1:
        return None
    survivor = left[0]
    if owner_of[survivor] != spender and group_size[owner_of[survivor]] > 1:
        return None  # co-owned with addresses that are not the spender's: leave it where it is
    return survivor


def cluster_entities(flows: FlowIndex, coinjoin_txids: set[str], use_change: bool = True,
                     structures: list[tuple[str, list[str]]] | None = None, use_elimination: bool = True) -> EntityClustering:
    """Entities from common-input ownership, change addresses and laundering structures.

    ``structures`` are (pattern id, addresses) groups that a detected peeling chain or layering
    structure shows to be one controller: the remainder of a peel *is* the spender's change, and
    funds that fan out and reconverge almost in full never left their owner's hands.
    CoinJoin transactions are excluded from the co-spend and change heuristics.
    """
    forest, evidence, counts = _UnionFind(), {}, Counter()
    excluded = _co_spend(flows, coinjoin_txids, forest, evidence, counts)
    unresolved = []
    if use_change:
        for tx in flows.txs:
            if tx.txid in coinjoin_txids:
                continue
            found = change_output(flows, tx)
            if found is None:
                unresolved.append(tx)
                continue
            address, heuristic = found
            if forest.union(tx.input_addresses[0], address):
                counts[heuristic] += 1
            evidence.setdefault(address, [heuristic, tx.txid])
    for pattern_id, addresses in structures or ():
        known = [address for address in addresses if address in flows.first_seen]
        for address in known[1:]:
            if forest.union(known[0], address):
                counts[LINK_STRUCTURE] += 1
        for address in known if len(known) > 1 else ():
            evidence.setdefault(address, [LINK_STRUCTURE, pattern_id])
    if use_change and use_elimination and unresolved:
        # Second pass, judged against the entities found so far (decisions do not depend on each other's order).
        owner_of = {address: forest.find(address) for address in flows.first_seen}
        group_size = Counter(owner_of.values())
        services = _service_groups(flows, owner_of)
        if services:
            for tx in unresolved:
                address = change_by_elimination(flows, tx, owner_of, group_size, services)
                if address is None:
                    continue
                if forest.union(tx.input_addresses[0], address):
                    counts[LINK_CHANGE_ELIMINATION] += 1
                evidence.setdefault(address, [LINK_CHANGE_ELIMINATION, tx.txid])
    return _finish(flows, forest, excluded, evidence, counts)


@dataclass
class ActorGroups:
    """Entities joined into one actor by network and money-flow evidence together."""

    actor_of: dict[str, str]                 # entity id -> actor id ("A<n>")
    members: dict[str, list[str]]            # actor id -> entity ids
    # entity id -> the link that attached it: {"kind", "txid", "with", "ip" ("" when no IP was needed)[, "address"]}
    evidence: dict[str, dict[str, str]]
    skipped_ips: list[str] = field(default_factory=list)   # IPs too widely shared to say anything
    link_counts: dict[str, int] = field(default_factory=dict)   # merges by kind of evidence


ACTOR_LINK_TEXT = {
    "payment": "broadcast from the same IP, and one pays the other directly",
    "deposit": "broadcast from the same IP, and both pay into the same service deposit address",
    "payment+deposit": "one pays the other directly, and both pay into the same service deposit address",
}


def link_actors(flows: FlowIndex, origins: list, entity_of: dict[str, str], exclude_txids: set[str] | frozenset[str] = frozenset(),
                service: dict[str, float] | None = None, max_entities_per_ip: int = 50, service_limit: float = SERVICE_LIMIT,
                max_depositors: int = MAX_DEPOSITORS, use_deposits: bool = True) -> ActorGroups:
    """Group entities that are operated together: two independent clues must agree.

    No single clue is enough. Unrelated people share IPs (NAT, VPN exits, shared hosting), anyone pays
    anyone, and several people can pay one address. Three clues are used, and any two of them together
    describe one operator moving funds between its own wallets - the links the ownership heuristics
    cannot prove (ambiguous change, self-transfers to fresh addresses, a mule run from the operator's
    machines, an operator who hops between unrelated networks):

    * **same IP** - both broadcast transactions from the same IP address;
    * **payment** - one pays the other directly;
    * **deposit** - both pay into the same address of a service. Exchanges give each customer a deposit
      address, so wallets that fund one deposit address fund one account. Addresses paid by more than
      ``max_depositors`` entities are public payment addresses and say nothing.

    Entities are joined on *same IP + payment*, *same IP + deposit* or *payment + deposit*. Groups are
    grown to a fixed point, so an operator rotating through a pool of IPs is still assembled as its pool
    is discovered.

    ``origins`` is ``NetworkCorrelation.origins``; only confidently attributed, non-excluded transactions
    count. Service-like entities and IPs used by more than ``max_entities_per_ip`` entities are left out:
    co-hosted services and public proxies tell nothing about who controls what.
    """
    service = service or {}

    def is_service(entity: str) -> bool:
        return service.get(entity, 0.0) >= service_limit

    entity_ips: dict[str, set[str]] = defaultdict(set)
    ip_entities: dict[str, set[str]] = defaultdict(set)
    for tx in flows.txs:
        origin = origins[tx.index] if origins else None
        if origin is None or not origin.attributed or tx.txid in exclude_txids:
            continue
        for entity in {entity_of[address] for address in tx.input_addresses}:
            if not is_service(entity):
                entity_ips[entity].add(origin.ip)
                ip_entities[origin.ip].add(entity)
    skipped = sorted(ip for ip, entities in ip_entities.items() if len(entities) > max_entities_per_ip)
    pool = {entity: ips.difference(skipped) for entity, ips in entity_ips.items()}

    payments: dict[tuple[str, str], str] = {}                    # unordered pair -> first transaction in which one pays the other
    depositors: dict[str, dict[str, str]] = defaultdict(dict)    # service address -> paying entity -> its first deposit
    for tx in flows.txs:
        if tx.txid in exclude_txids:
            continue
        senders = {entity for entity in {entity_of[address] for address in tx.input_addresses} if not is_service(entity)}
        if not senders:
            continue
        for address in tx.output_addresses:
            receiver = entity_of[address]
            if is_service(receiver):
                if use_deposits:
                    for sender in senders:
                        depositors[address].setdefault(sender, tx.txid)
                continue
            for sender in senders:
                if sender != receiver:
                    payments.setdefault((sender, receiver) if sender < receiver else (receiver, sender), tx.txid)
    deposits: dict[tuple[str, str], tuple[str, str]] = {}        # unordered pair -> (a deposit transaction, the shared address)
    for address, paid_by in depositors.items():
        if 2 <= len(paid_by) <= max_depositors:
            entities = sorted(paid_by)
            for position, first in enumerate(entities):
                for second in entities[position + 1:]:
                    deposits.setdefault((first, second), (paid_by[first], address))

    forest = _UnionFind()
    evidence: dict[str, dict[str, str]] = {}
    counts: Counter = Counter()

    def join(first: str, second: str, link: dict[str, str]) -> None:
        a, b = forest.find(first), forest.find(second)
        forest.union(a, b)
        merged = pool.get(a, set()) | pool.get(b, set())
        if merged:
            pool[forest.find(a)] = merged
        evidence.setdefault(first, {**link, "with": second})
        evidence.setdefault(second, {**link, "with": first})
        counts[link["kind"]] += 1

    # Two money ties need no network evidence.
    for pair, txid in payments.items():
        shared_address = deposits.get(pair)
        if shared_address is not None and forest.find(pair[0]) != forest.find(pair[1]):
            join(pair[0], pair[1], {"kind": "payment+deposit", "txid": txid, "ip": "", "address": shared_address[1]})
    # One money tie needs the same IP. Merging enlarges a group's IP pool, which can enable further merges.
    ties = [(pair, txid, "payment", "") for pair, txid in payments.items()]
    ties += [(pair, txid, "deposit", address) for pair, (txid, address) in deposits.items() if pair not in payments]
    changed = True
    while changed:
        changed = False
        for (first, second), txid, kind, address in ties:
            a, b = forest.find(first), forest.find(second)
            if a == b:
                continue
            shared = pool.get(a, set()) & pool.get(b, set())
            if not shared:
                continue
            link = {"kind": kind, "txid": txid, "ip": min(shared)}
            if address:
                link["address"] = address
            join(first, second, link)
            changed = True
    groups: dict[str, list[str]] = defaultdict(list)
    for entity in dict.fromkeys(entity_of.values()):
        groups[forest.find(entity)].append(entity)
    ordered = sorted(groups.values(), key=lambda items: (-len(items), items[0]))
    actor_of, members = {}, {}
    for number, entities in enumerate(ordered, start=1):
        actor_id = f"A{number}"
        members[actor_id] = entities
        for entity in entities:
            actor_of[entity] = actor_id
    return ActorGroups(actor_of, members, evidence, skipped, dict(counts))


def entity_graph(flows: FlowIndex, clustering: EntityClustering) -> tuple[list[str], sparse.csr_matrix]:
    """Undirected entity-to-entity graph weighted by the number of payments between them."""
    entities = list(clustering.members)
    position = {entity: index for index, entity in enumerate(entities)}
    entity_of = clustering.entity_of
    weights: Counter = Counter()
    for tx in flows.txs:
        senders = {position[entity_of[address]] for address in tx.input_addresses}
        receivers = {position[entity_of[address]] for address in tx.output_addresses}
        for sender in senders:
            for receiver in receivers:
                if sender != receiver:
                    weights[(sender, receiver) if sender < receiver else (receiver, sender)] += 1
    if not weights:
        return entities, sparse.csr_matrix((len(entities), len(entities)))
    pairs = np.array(list(weights), dtype=np.int64)
    data = np.fromiter(weights.values(), dtype=np.float64, count=len(weights))
    matrix = sparse.coo_matrix((data, (pairs[:, 0], pairs[:, 1])), shape=(len(entities), len(entities)))
    return entities, (matrix + matrix.T).tocsr()


def deepwalk_embeddings(adjacency: sparse.csr_matrix, dimensions: int = 16, walks_per_node: int = 6, walk_length: int = 16,
                        window: int = 4, random_state: int = 42) -> np.ndarray:
    """DeepWalk-style embeddings from sampled walks, a PPMI co-occurrence matrix and truncated SVD."""
    n = adjacency.shape[0]
    if n == 0:
        return np.zeros((0, dimensions))
    rng = np.random.default_rng(random_state)
    adjacency = adjacency.tocsr()
    degree = np.asarray(adjacency.sum(axis=1)).ravel()
    isolated = degree == 0
    # Isolated nodes walk in place so every row of the co-occurrence matrix is defined.
    adjacency = (adjacency + sparse.diags(isolated.astype(np.float64))).tocsr()
    degree = np.asarray(adjacency.sum(axis=1)).ravel()
    cumulative = np.cumsum(adjacency.data)
    row_offset = np.concatenate([[0.0], cumulative])[adjacency.indptr[:-1]]
    walkers = np.repeat(np.arange(n), walks_per_node)
    walk = np.empty((walkers.size, walk_length), dtype=np.int64)
    walk[:, 0] = walkers
    current = walkers
    for step in range(1, walk_length):
        target = row_offset[current] + rng.random(current.size) * degree[current]
        edge = np.searchsorted(cumulative, target, side="right")
        edge = np.minimum(edge, adjacency.indptr[current + 1] - 1)
        current = adjacency.indices[edge]
        walk[:, step] = current
    rows, cols = [], []
    for offset in range(1, window + 1):
        rows.append(walk[:, :-offset].ravel())
        cols.append(walk[:, offset:].ravel())
    rows_all = np.concatenate(rows + cols)
    cols_all = np.concatenate(cols + rows)
    counts = sparse.coo_matrix((np.ones(rows_all.size), (rows_all, cols_all)), shape=(n, n)).tocsr()
    counts.sum_duplicates()
    # Pairs that co-occur once are mostly sampling noise; dropping them keeps the matrix sparse.
    counts.data[counts.data < 2] = 0
    counts.eliminate_zeros()
    total = counts.sum()
    row_sums = np.asarray(counts.sum(axis=1)).ravel()
    col_sums = np.asarray(counts.sum(axis=0)).ravel()
    coo = counts.tocoo()
    pmi = np.log(coo.data * total / (row_sums[coo.row] * col_sums[coo.col]))
    keep = pmi > 0
    ppmi = sparse.coo_matrix((pmi[keep], (coo.row[keep], coo.col[keep])), shape=(n, n)).tocsr()
    components = max(2, min(dimensions, n - 1))
    if ppmi.nnz == 0 or n <= 2:
        return np.zeros((n, dimensions))
    svd = TruncatedSVD(n_components=components, n_iter=4, random_state=random_state)
    embedding = svd.fit_transform(ppmi)
    if components < dimensions:
        embedding = np.hstack([embedding, np.zeros((n, dimensions - components))])
    return embedding


@dataclass
class Communities:
    community_of: dict[str, int]      # entity id -> community id (-1 = unassigned)
    embedding: dict[str, list[float]]  # entity id -> embedding (for inspection / export)


def detect_communities(flows: FlowIndex, clustering: EntityClustering, random_state: int = 42) -> Communities:
    entities, adjacency = entity_graph(flows, clustering)
    embedding = deepwalk_embeddings(adjacency, random_state=random_state)
    degree = np.asarray(adjacency.sum(axis=1)).ravel() if adjacency.shape[0] else np.zeros(0)
    community_of = {entity: -1 for entity in entities}
    connected = np.flatnonzero(degree > 0)
    if connected.size >= 10:
        # Density clustering degrades badly in many dimensions; cluster on the leading components.
        vectors = PCA(n_components=min(8, connected.size - 1), random_state=random_state).fit_transform(normalize(embedding[connected]))
        fitted = np.arange(connected.size)
        if connected.size > MAX_DENSITY_POINTS:  # fit on the best-connected entities, assign the rest afterwards
            fitted = np.sort(np.argsort(-degree[connected], kind="stable")[:MAX_DENSITY_POINTS])
        min_size = max(5, int(round(fitted.size ** 0.5 / 3)))
        labels = np.full(connected.size, -1, dtype=np.int64)
        labels[fitted] = HDBSCAN(min_cluster_size=min_size, min_samples=3, copy=True).fit_predict(vectors[fitted])
        if fitted.size < connected.size and (labels >= 0).any():
            labels = _assign_remaining(vectors, labels, fitted)
        # Renumber communities by size so community 0 is the largest.
        order = [label for label, _ in Counter(int(label) for label in labels if label >= 0).most_common()]
        renumber = {label: index for index, label in enumerate(order)}
        for node, label in zip(connected, labels):
            community_of[entities[node]] = renumber.get(int(label), -1)
    return Communities(community_of, {entity: [round(float(value), 5) for value in embedding[index][:8]] for index, entity in enumerate(entities)})


def _assign_remaining(vectors: np.ndarray, labels: np.ndarray, fitted: np.ndarray) -> np.ndarray:
    """Give entities outside the density fit the label of the nearest community centre, if it is close enough."""
    found = np.unique(labels[labels >= 0])
    centres = np.vstack([vectors[fitted][labels[fitted] == label].mean(axis=0) for label in found])
    # A community's radius is the 90th percentile distance of its own members to the centre.
    radius = np.array([np.percentile(np.linalg.norm(vectors[fitted][labels[fitted] == label] - centre, axis=1), 90) for label, centre in zip(found, centres)])
    rest = np.setdiff1d(np.arange(vectors.shape[0]), fitted, assume_unique=True)
    result = labels.copy()
    for start in range(0, rest.size, 20000):
        chunk = rest[start:start + 20000]
        distance = np.linalg.norm(vectors[chunk][:, None, :] - centres[None, :, :], axis=2)
        nearest = distance.argmin(axis=1)
        close = distance[np.arange(chunk.size), nearest] <= radius[nearest]
        result[chunk[close]] = found[nearest[close]]
    return result


__all__ = ["EntityClustering", "ActorGroups", "link_actors", "common_input_ownership", "cluster_entities", "change_output", "change_by_elimination",
           "entity_graph", "deepwalk_embeddings", "Communities", "detect_communities", "LINK_CO_SPEND", "LINK_CHANGE_ONLY_NEW", "LINK_CHANGE_SCRIPT",
           "LINK_CHANGE_ELIMINATION", "LINK_STRUCTURE", "SERVICE_LIMIT", "MAX_DEPOSITORS", "ACTOR_LINK_TEXT"]
