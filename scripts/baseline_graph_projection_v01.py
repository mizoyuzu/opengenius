"""Check a typed graph projection against all assigned Genius state rows."""


def validate_projection(graph, state):
    """Allow omitted empty assignments, never omitted or changed relations."""
    if set(graph) - set(state):
        raise ValueError('Baseline graph has a PID missing from state')
    if any(state[pid] != targets for pid, targets in graph.items()):
        raise ValueError('Baseline graph/state ordered relations differ')
    if any(targets for pid, targets in state.items() if pid not in graph):
        raise ValueError('Baseline graph omits a nonempty state root')
    return len(state)
