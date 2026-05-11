# src/orchestrator/algorithms/entropy_dijkstra.py
import heapq

def entropy_aware_dijkstra(graph, source, destination, gamma=0.5):
    """
    [span_5](start_span)Algorithm 1: Quantum-Aware Task Orchestration (QATO)[span_5](end_span).
    [span_6](start_span)Calculates W_total = W_classical + gamma * (S_phys / S_sh)[span_6](end_span).
    """
    pq = [(0, source, [])]
    visited = set()
    
    while pq:
        (cost, current_node, path) = heapq.heappop(pq)
        
        if current_node in visited:
            continue
            
        path = path + [current_node]
        if current_node == destination:
            return path, cost
            
        visited.add(current_node)
        
        for neighbor, weight in graph[current_node].items():
            # [span_7](start_span)Retrieve entropy metrics from telemetry layer[span_7](end_span)
            s_phys = neighbor.get_physical_hamiltonian_entropy()
            s_sh = neighbor.get_shannon_entropy()
            
            # [span_8](start_span)Security-Cost weight derivation[span_8](end_span)
            w_security = s_phys / (s_sh + 1e-9) 
            total_weight = weight + (gamma * w_security)
            
            heapq.heappush(pq, (cost + total_weight, neighbor, path))
            
    return None, float("inf")
