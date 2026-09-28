/**
 * Type definitions and interfaces for the frontend application.
 */

// This file serves as documentation for the expected data structures
// from the backend API.

export const DataTypes = {
  // Stats response
  Stats: {
    total_transactions: Number,
    unique_wallets: Number,
    unique_ips: Number,
    behavior_types: [String],
  },

  // Alert/Risk score object
  Alert: {
    wallet_id: String,
    risk_score: Number,
    risk_level: String, // LOW, MEDIUM, HIGH, CRITICAL
    confidence: Number,
    cluster_id: Number,
    top_reasons: [{ label: String, confidence: Number, evidence: String, investigative_lead: String }],
  },

  // Entity details
  Entity: {
    wallet_id: String,
    risk_score: Number,
    risk_level: String,
    confidence: Number,
    cluster_id: Number,
    transaction_statistics: {
      transaction_count: Number,
      incoming_transaction_count: Number,
      outgoing_transaction_count: Number,
    },
    network_statistics: {
      unique_ips: Number,
      unique_counterparties: Number,
      graph_degree: Number,
    },
    ml_information: {
      anomaly_score: Number,
      anomaly_label: Number,
    },
    cluster_information: {
      cluster_id: Number,
    },
    explanations: [Object],
    evidence: Object,
    related_entities: [String],
  },

  // Evidence object
  Evidence: {
    observed_fact: Object,
    inference: String,
    investigative_lead: String,
  },

  // Transaction
  Transaction: {
    txid: String,
    timestamp: String,
    src_ip: String,
    dst_ip: String,
    input_addresses: [String],
    output_addresses: [String],
    fee: Number,
  },

  // Cluster
  Cluster: {
    cluster_id: Number,
    wallet_id: String,
  },

  // Graph node
  GraphNode: {
    id: String,
    type: String, // Wallet, Transaction, IP, etc.
    relationship: String,
  },
};

export default DataTypes;
