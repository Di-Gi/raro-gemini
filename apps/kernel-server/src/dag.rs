// [[RARO]]/apps/kernel-server/src/dag.rs
// Purpose: DAG Data Structure. Updated with mutation methods for dynamic graph splicing.
// Architecture: Core Data Structure
// Dependencies: std, thiserror

use std::collections::{HashMap, HashSet, VecDeque};
use thiserror::Error;

#[derive(Error, Debug)]
pub enum DAGError {
    #[error("Cycle detected in DAG")]
    CycleDetected,
    #[error("Invalid node: {0}")]
    InvalidNode(String),
    #[error("Dependency not found: {0}")]
    DependencyNotFound(String),
    #[error("Edge not found: {0} -> {1}")]
    EdgeNotFound(String, String),
}

#[derive(Clone, Debug)] // Added Clone/Debug for easier state management
pub struct DAG {
    nodes: HashSet<String>,
    edges: HashMap<String, Vec<String>>, // Adjacency list: Source -> [Targets]
}

impl DAG {
    pub fn new() -> Self {
        DAG {
            nodes: HashSet::new(),
            edges: HashMap::new(),
        }
    }

    /// Add a node to the DAG
    pub fn add_node(&mut self, node_id: String) -> Result<(), DAGError> {
        self.nodes.insert(node_id);
        Ok(())
    }

    /// Add an edge from source to target
    pub fn add_edge(&mut self, from: String, to: String) -> Result<(), DAGError> {
        if !self.nodes.contains(&from) {
            return Err(DAGError::InvalidNode(from));
        }
        if !self.nodes.contains(&to) {
            return Err(DAGError::InvalidNode(to));
        }

        // Check for cycle before adding
        if self.would_create_cycle(&from, &to) {
            return Err(DAGError::CycleDetected);
        }

        self.edges.entry(from).or_insert_with(Vec::new).push(to);
        Ok(())
    }

    /// Remove an edge from source to target (Required for splicing)
    pub fn remove_edge(&mut self, from: &str, to: &str) -> Result<(), DAGError> {
        if let Some(targets) = self.edges.get_mut(from) {
            if let Some(pos) = targets.iter().position(|x| x == to) {
                targets.remove(pos);
                return Ok(());
            }
        }
        Err(DAGError::EdgeNotFound(from.to_string(), to.to_string()))
    }

    /// Get all direct children (dependents) of a node
    pub fn get_children(&self, node_id: &str) -> Vec<String> {
        self.edges.get(node_id).cloned().unwrap_or_default()
    }

    /// Check if adding edge would create a cycle
    fn would_create_cycle(&self, from: &str, to: &str) -> bool {
        // DFS from 'to' to see if we can reach 'from'
        let mut visited = HashSet::new();
        self.has_path_dfs(to, from, &mut visited)
    }

    fn has_path_dfs(
        &self,
        current: &str,
        target: &str,
        visited: &mut HashSet<String>,
    ) -> bool {
        if current == target {
            return true;
        }

        if visited.contains(current) {
            return false;
        }

        visited.insert(current.to_string());

        if let Some(neighbors) = self.edges.get(current) {
            for neighbor in neighbors {
                if self.has_path_dfs(neighbor, target, visited) {
                    return true;
                }
            }
        }

        false
    }

    /// Compute topological order for execution
    /// This is now used dynamically to recalculate the path after mutation
    pub fn topological_sort(&self) -> Result<Vec<String>, DAGError> {
        let mut in_degree: HashMap<String, usize> = self.nodes.iter().map(|n| (n.clone(), 0)).collect();

        for neighbors in self.edges.values() {
            for neighbor in neighbors {
                *in_degree.get_mut(neighbor).unwrap() += 1;
            }
        }

        let mut queue: VecDeque<String> = in_degree
            .iter()
            .filter(|(_, &degree)| degree == 0)
            .map(|(node, _)| node.clone())
            .collect();

        let mut result = Vec::new();

        while let Some(node) = queue.pop_front() {
            result.push(node.clone());

            if let Some(neighbors) = self.edges.get(&node) {
                for neighbor in neighbors {
                    let degree = in_degree.get_mut(neighbor).unwrap();
                    *degree -= 1;
                    if *degree == 0 {
                        queue.push_back(neighbor.clone());
                    }
                }
            }
        }

        if result.len() != self.nodes.len() {
            return Err(DAGError::CycleDetected);
        }

        Ok(result)
    }

    /// Get dependencies for a given node (Reverse lookup)
    pub fn get_dependencies(&self, node_id: &str) -> Vec<String> {
        let mut deps = Vec::new();
        for (source, targets) in &self.edges {
            if targets.contains(&node_id.to_string()) {
                deps.push(source.clone());
            }
        }
        deps
    }
    
    /// Export edges as a flat vector for UI visualization
    pub fn export_edges(&self) -> Vec<(String, String)> {
        let mut edge_list = Vec::new();
        for (source, targets) in &self.edges {
            for target in targets {
                edge_list.push((source.clone(), target.clone()));
            }
        }
        edge_list
    }

    /// Export all known node IDs
    pub fn export_nodes(&self) -> Vec<String> {
        self.nodes.iter().cloned().collect()
    }

    /// Get dependents for a given node
    pub fn get_dependents(&self, node_id: &str) -> Option<Vec<String>> {
        self.edges.get(node_id).cloned()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_topological_sort() {
        let mut dag = DAG::new();
        dag.add_node("a".to_string()).unwrap();
        dag.add_node("b".to_string()).unwrap();
        dag.add_node("c".to_string()).unwrap();

        dag.add_edge("a".to_string(), "b".to_string()).unwrap();
        dag.add_edge("b".to_string(), "c".to_string()).unwrap();

        let order = dag.topological_sort().unwrap();
        assert_eq!(order, vec!["a", "b", "c"]);
    }

    #[test]
    fn test_cycle_detection() {
        let mut dag = DAG::new();
        dag.add_node("a".to_string()).unwrap();
        dag.add_node("b".to_string()).unwrap();

        dag.add_edge("a".to_string(), "b".to_string()).unwrap();
        let result = dag.add_edge("b".to_string(), "a".to_string());

        assert!(result.is_err());
    }
}
