"""
MAGE algorithm runner — the ONLY place in the codebase where MAGE CALL procedures appear.
Never add CALL module.procedure() statements anywhere else.
"""
from __future__ import annotations

import structlog

from transform_service import memgraph_client
from transform_service.utils import with_retry

log = structlog.get_logger()

_FAST_ALGORITHMS = [
    (
        "pagerank",
        "CALL pagerank.get() YIELD node, rank SET node.pagerank_score = rank",
    ),
    (
        "community_detection",
        "CALL community_detection.get() YIELD node, community_id SET node.community_id = community_id",
    ),
    (
        "betweenness_centrality",
        "CALL betweenness_centrality.get() YIELD node, betweenness_centrality SET node.betweenness_centrality = betweenness_centrality",
    ),
    (
        "degree_centrality",
        "CALL degree_centrality.get() YIELD node, degree AS degree_centrality SET node.degree_centrality = degree_centrality",
    ),
    (
        "wcc",
        "CALL weakly_connected_components.get() YIELD node, component_id SET node.wcc_id = component_id",
    ),
]

_FULL_ALGORITHMS = [
    (
        "pagerank",
        "CALL pagerank.get() YIELD node, rank SET node.pagerank_score = rank",
    ),
    (
        "leiden_community_detection",
        # objective_function="modularity" (not the "CPM" default at resolution_parameter=1)
        # -- CPM at resolution 1 over-fragments a graph this sparse into all-singleton
        # communities (confirmed live: 308/308 communities of size 1), silently corrupting
        # every insight endpoint until the next per-meeting run_fast_algorithms() call
        # (Louvain) happens to overwrite it. This runs on a live nightly schedule.
        'CALL igraphalg.community_leiden("modularity") YIELD node, community_id '
        "SET node.community_id = community_id",
    ),
    (
        "betweenness_centrality",
        "CALL betweenness_centrality.get() YIELD node, betweenness_centrality SET node.betweenness_centrality = betweenness_centrality",
    ),
    (
        "degree_centrality",
        "CALL degree_centrality.get() YIELD node, degree AS degree_centrality SET node.degree_centrality = degree_centrality",
    ),
    (
        "wcc",
        "CALL weakly_connected_components.get() YIELD node, component_id SET node.wcc_id = component_id",
    ),
]


@with_retry(max_attempts=2, base_delay=1.0)
async def _run_one(session, cypher: str) -> None:
    """One algorithm CALL, retried once on Memgraph's transient write-write conflict
    (real and reproduced live: a concurrent per-meeting run_fast_algorithms() call can
    collide with a scheduled run_full_algorithms() writing the same score properties)."""
    result = await session.run(cypher)
    await result.consume()


async def _run_algorithms(session, algorithms: list[tuple[str, str]]) -> dict[str, str]:
    """Run each algorithm's Cypher and fully consume the result before moving
    to the next one. Consuming is required — the async driver otherwise defers
    execution and surfaces a failing statement's error on the *next* session.run()
    call, misattributing it to the wrong algorithm."""
    results: dict[str, str] = {}
    for name, cypher in algorithms:
        try:
            await _run_one(session, cypher)
            results[name] = "ok"
        except Exception as exc:
            log.warning("graph_algorithms.algorithm_failed", algorithm=name, error=str(exc))
            results[name] = f"failed: {exc}"
    return results


async def run_fast_algorithms() -> dict:
    """Event-driven path — called after each processed batch.
    Uses Louvain community_detection (faster) for speed.
    Failures per algorithm are caught individually so one bad call
    never aborts the others."""
    driver = memgraph_client.get_driver()
    async with driver.session() as session:
        results = await _run_algorithms(session, _FAST_ALGORITHMS)

    log.info("graph_algorithms.fast_run_complete", results=results)
    return {
        "algorithms_run": [name for name, _ in _FAST_ALGORITHMS],
        "results": results,
    }


async def run_full_algorithms() -> dict:
    """Nightly path — uses leiden (igraphalg.community_leiden, more accurate)
    and recomputes all five algorithms over the full graph."""
    driver = memgraph_client.get_driver()
    async with driver.session() as session:
        results = await _run_algorithms(session, _FULL_ALGORITHMS)

    log.info("graph_algorithms.full_run_complete", results=results)
    return {
        "algorithms_run": [name for name, _ in _FULL_ALGORITHMS],
        "results": results,
    }


async def get_jaccard_similarity(node_id_a: str, node_id_b: str) -> float:
    """On-demand: Jaccard similarity between two nodes based on shared neighbors.
    Used by procedural memory's discover_procedures().
    node_similarity.jaccard() takes no node arguments (it streams over the whole
    graph) — the pairwise variant is required to score a specific pair."""
    driver = memgraph_client.get_driver()
    async with driver.session() as session:
        result = await session.run(
            """
            MATCH (a {id: $id_a}), (b {id: $id_b})
            CALL node_similarity.jaccard_pairwise([a], [b])
            YIELD similarity
            RETURN similarity
            """,
            id_a=node_id_a,
            id_b=node_id_b,
        )
        scores = [record["similarity"] async for record in result]
        return max(scores) if scores else 0.0


async def vector_search(index_name: str, query_vector: list[float], limit: int = 5) -> list[dict]:
    """On-demand: nearest-neighbor search against a MAGE vector index.
    Used by vector_memory.py — never call this MAGE procedure directly
    from another module. Returns [{node_id, similarity, distance}, ...]
    ordered by similarity descending (best match first)."""
    driver = memgraph_client.get_driver()
    async with driver.session() as session:
        result = await session.run(
            """
            CALL vector_search.search($index_name, $limit, $query_vector)
            YIELD node, similarity, distance
            RETURN node.id AS node_id, similarity, distance
            ORDER BY similarity DESC
            """,
            index_name=index_name,
            limit=limit,
            query_vector=query_vector,
        )
        return [dict(r) async for r in result]
