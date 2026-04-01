# SBPS iSCSI Metrics Collection Methods

## 1. Overview

This document describes multiple approaches to collect cluster-wide metrics from worker nodes into a master node in a Kubernetes-based environment.

### Metrics to Collect

1. List of worker nodes configured as iSCSI targets
2. Number of projected rootfs / PE images per worker node
3. Total number of projected images (cluster-wide)
4. Number of deleted images per worker node

---

## 2. System Context

- OS: SLES (SUSE Linux Enterprise Server)
- Orchestration: Kubernetes
- Storage: iSCSI (targetcli / open-iscsi)
- Nodes:
  - 1 Master Node
  - 4 Worker Nodes

---

## 3. Data Model

Each node maintains:

/var/lib/sbps/
    ├── active/      # Active (projected) images
    └── deleted/     # Deleted images

---

## 4. Method 1: DaemonSet + REST API (Pull Model) (Best approach according to me)

### 4.1 Architecture

Each node runs an agent (via DaemonSet) exposing:

GET /metrics

Master node queries all agents and aggregates results.

### 4.2 Workflow

1. DaemonSet deploys agent on all nodes
2. Agent collects:
   - Active images count
   - Deleted images count
   - iSCSI target status
3. Master queries all nodes
4. Aggregates results

### 4.3 Implementation Details

#### Agent Responsibilities

- Read filesystem
- Check iSCSI service
- Return JSON response

#### Master Responsibilities

- Discover nodes
- Call APIs
- Aggregate metrics

---

### 4.4 Advantages

- Kubernetes-native
- Highly scalable
- Fault-tolerant
- Easy to extend

---

### 4.5 Disadvantages

- Requires API service on each node
- Slight network overhead

---

### 4.6 Best Practices

- Use timeouts and retries
- Use Kubernetes DNS instead of IPs
- Secure API with authentication 

---

## 5. Method 2: Prometheus-Based Monitoring (Pull Model - Metrics Scraping)

### 5.1 Architecture

- Agents expose `/metrics` in Prometheus format
- Prometheus scrapes metrics
- Master queries Prometheus API

### 5.2 Workflow

1. Node agent exposes metrics
2. Prometheus scrapes periodically
3. Master queries Prometheus

---

### 5.3 Example Metrics

sbps_active_images{node="worker1"} 3  
sbps_deleted_images{node="worker1"} 1  
sbps_iscsi_target{node="worker1"} 1  

---

### 5.4 Advantages

- Industry standard (used at scale)
- Time-series data available
- Easy integration with Grafana

---

### 5.5 Disadvantages

- More complex setup
- Requires Prometheus infrastructure

---

### 5.6 Use Case

Recommended when:
- Historical metrics needed
- Visualization required
- Large clusters

---

## 6. Method 3: Kubernetes Custom Resource (CRD-Based Approach)

### 6.1 Architecture

- Define Custom Resource: SBPSMetrics
- Each node updates its CRD object
- Master reads CRDs

---

### 6.2 Workflow

1. Define CRD schema
2. Node agent updates CRD
3. Master queries CRD objects

---

### 6.3 Advantages

- Fully Kubernetes-native
- No external API needed
- Declarative model

---

### 6.4 Disadvantages

- Complex to implement
- Requires RBAC and controller logic

---

### 6.5 Use Case

Recommended when:
- Deep Kubernetes integration required
- Enterprise-grade control plane integration

---

## 7. Method 4: SSH-Based Remote Execution

### 7.1 Architecture

Master node connects to workers via SSH and executes commands.

---

### 7.2 Example Commands

ssh worker1 "ls /var/lib/sbps/active | wc -l"  
ssh worker1 "systemctl is-active target"  

---

### 7.3 Advantages

- Simple to implement
- No additional services needed

---

### 7.4 Disadvantages

- Not scalable
- Security risks
- High latency

---

### 7.5 Use Case

Only suitable for:
- Small lab setups
- Debugging

---

## 8. Method 5: Message Queue / Push Model

### 8.1 Architecture

- Nodes push metrics to central queue (Kafka / RabbitMQ)
- Master consumes messages

---

### 8.2 Workflow

1. Agent collects metrics
2. Sends to queue
3. Master processes messages

---

### 8.3 Advantages

- Decoupled architecture
- Highly scalable
- Real-time streaming

---

### 8.4 Disadvantages

- Complex setup
- Requires message broker

---

### 8.5 Use Case

Recommended when:
- Real-time processing required
- Large distributed systems

---

## 9. Comparison Summary

| Method | Complexity | Scalability | Best Use Case |
|--------|-----------|------------|--------------|
| DaemonSet + API | Medium | High | Recommended default |
| Prometheus | High | Very High | Monitoring + dashboards |
| CRD | High | High | Kubernetes-native systems |
| SSH | Low | Low | Testing only |
| Message Queue | Very High | Very High | Real-time systems |

---



---

## 11. Security Considerations

- Use RBAC for Kubernetes API access
- Restrict API endpoints (internal cluster only)
- Use TLS for communication
- Avoid exposing node-level services externally

---

## 12. Failure Handling

- Node unreachable → skip and log
- Timeout handling (2–3 seconds)
- Retry mechanism (max 3 retries)

---

## 13. Performance Considerations

- Batch API calls
- Use async requests (Python asyncio)
- Avoid frequent polling

---


---

## 15. References

### Kubernetes
https://kubernetes.io/docs/concepts/workloads/controllers/daemonset/  
https://kubernetes.io/docs/concepts/services-networking/service/  

### Prometheus
https://prometheus.io/docs/introduction/overview/  

### iSCSI
https://linux-iscsi.org/wiki/Targetcli  
https://linux-iscsi.org/  

### SLES
https://documentation.suse.com/  

### FastAPI
https://fastapi.tiangolo.com/  

---
