"""Entity clustering: common-input ownership plus graph-embedding communities.

1. **Common-input ownership (CIO).** Addresses spent together as inputs of one
   transaction are controlled by the same entity (they had to be signed by the
   same wallet). Union-find merges them transitively. CoinJoin transactions are
   excluded because their inputs deliberately belong to different people, which
   is the main way this heuristic goes wrong.
2. **Graph embeddings.** Entities become nodes of a weighted transaction graph
   (an edge per payment between two entities). DeepWalk is approximated as an
   explicit matrix factorisation: truncated random walks are sampled, window
   co-occurrences are counted (pairs seen once are dropped as sampling noise),
   turned into a positive PMI matrix and factorised
   with truncated SVD (Qiu et al., "Network Embedding as Matrix Factorization").
3. **Communities.** HDBSCAN (on the 8 leading principal components of the
   embeddings) groups entities whose embeddings are close, i.e.
   entities that sit in the same part of the money-flow graph. Entities it cannot
   place confidently get community ``-1``.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

import numpy as np
from scipy import sparse
from sklearn.cluster import HDBSCAN
from sklearn.decomposition import PCA, TruncatedSVD
from sklearn.preprocessing import normalize

from backend.correlation.flows import FlowIndex


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

    def union(self, left: str, right: str) -> None:
        a, b = self.find(left), self.find(right)
        if a != b:
            self.parent[max(a, b)] = min(a, b)


@dataclass
class EntityClustering:
    entity_of: dict[str, str]
    members: dict[str, list[str]]
    excluded_coinjoins: int

    def size(self, address: str) -> int:
        return len(self.members.get(self.entity_of.get(address, ""), [])) or 1


def common_input_ownership(flows: FlowIndex, exclude_txids: set[str]) -> EntityClustering:
    forest = _UnionFind()
    excluded = 0
    for tx in flows.txs:
        addresses = tx.input_addresses
        if tx.txid in exclude_txids:
            excluded += 1
            continue
        for address in addresses[1:]:
            forest.union(addresses[0], address)
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
    return EntityClustering(entity_of, members, excluded)


def entity_graph(flows: FlowIndex, clustering: EntityClustering) -> tuple[list[str], sparse.csr_matrix]:
    """Undirected entity-to-entity graph weighted by the number of payments between them."""
    entities = list(clustering.members)
    position = {entity: index for index, entity in enumerate(entities)}
    weights: Counter = Counter()
    for tx in flows.txs:
        senders = {clustering.entity_of[address] for address in tx.input_addresses}
        receivers = {clustering.entity_of[address] for address in tx.output_addresses}
        for sender in senders:
            for receiver in receivers - {sender}:
                a, b = sorted((position[sender], position[receiver]))
                weights[(a, b)] += 1
    if not weights:
        return entities, sparse.csr_matrix((len(entities), len(entities)))
    rows, cols, data = zip(*((a, b, w) for (a, b), w in weights.items()))
    matrix = sparse.coo_matrix((data, (rows, cols)), shape=(len(entities), len(entities)))
    return entities, (matrix + matrix.T).tocsr().astype(np.float64)


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
        # Density clustering degrades badly in 32 dimensions; cluster on the leading components.
        vectors = PCA(n_components=min(8, connected.size - 1), random_state=random_state).fit_transform(normalize(embedding[connected]))
        min_size = max(5, int(round(connected.size ** 0.5 / 3)))
        labels = HDBSCAN(min_cluster_size=min_size, min_samples=3, copy=True).fit_predict(vectors)
        # Renumber communities by size so community 0 is the largest.
        order = [label for label, _ in Counter(label for label in labels if label >= 0).most_common()]
        renumber = {label: index for index, label in enumerate(order)}
        for node, label in zip(connected, labels):
            community_of[entities[node]] = renumber.get(int(label), -1)
    return Communities(community_of, {entity: [round(float(value), 5) for value in embedding[index][:8]] for index, entity in enumerate(entities)})


__all__ = ["EntityClustering", "common_input_ownership", "entity_graph", "deepwalk_embeddings", "Communities", "detect_communities"]
