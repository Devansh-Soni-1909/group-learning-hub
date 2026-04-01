# Kubernetes iSCSI Data Collection Design

## Overview

This document outlines system design approaches for collecting iSCSI-related data from Kubernetes worker nodes and aggregating it at the control plane. The target environment includes SLES or openSUSE with Kubernetes.

### Objectives

The system should retrieve the following attributes from worker nodes:

1. List of workers configured as iSCSI targets
2. Number of projected rootfs/PE images per worker node
3. Total number of projected images
4. Number of deleted images per worker node

---

## Recommended Approaches

### 1. DaemonSet with HTTP Endpoint (Recommended)

#### Architecture

Each worker node runs a collector agent deployed via a DaemonSet. The agent gathers local system and iSCSI data and exposes it through an HTTP endpoint.

#### Data Flow

- DaemonSet ensures one pod per worker node
- Each pod executes commands such as:
  - `targetcli ls`
  - `iscsiadm -m session`
  - Filesystem scans for image counts
- Data is exposed via an HTTP endpoint (e.g., `/metrics`)
- Control plane queries each node's endpoint

#### Example Output

```json
{
  "node": "iscsi-target-node",
  "iscsi_targets": 2,
  "projected_images": 10,
  "deleted_images": 3
}
```

#### Access Methods

- Direct pod access:

  ```bash
  curl http://<pod-ip>:8080/metrics
  ```

- Via Kubernetes Service:

  ```bash
  curl http://iscsi-monitor.default.svc.cluster.local/metrics
  ```

#### Advantages

- Real-time data collection
- Scales automatically with cluster
- Simple and widely used pattern

#### Limitations

- Requires service discovery
- Slight overhead per node

---

### 2. DaemonSet with Kubernetes CRD (Custom Resource)

#### Architecture

Instead of exposing HTTP endpoints, each node writes its data into a Kubernetes Custom Resource (CRD). The control plane reads from the Kubernetes API.

#### Data Flow

- DaemonSet runs collector on each node
- Collector uses Kubernetes API client
- Updates a CRD object per node
- Control plane queries CRDs

#### Example CRD

```yaml
apiVersion: storage.example.com/v1
kind: ISCSIStatus
metadata:
  name: iscsi-node-1
spec:
  targets: 2
  projected_images: 10
  deleted_images: 3
```

#### Access Methods

- Using kubectl:

  ```bash
  kubectl get iscsistatus -o yaml
  ```

- Using Kubernetes API:

  ```bash
  kubectl get --raw /apis/storage.example.com/v1/iscsistatus
  ```

#### Advantages

- Fully Kubernetes-native
- Secure via RBAC
- Clean architecture

#### Limitations

- Higher implementation complexity
- Requires CRD and API integration

---

### 3. Prometheus Metrics Scraping

#### Architecture

Each node exposes metrics in Prometheus format. A Prometheus server periodically scrapes these metrics and stores them.

#### Data Flow

- DaemonSet exposes `/metrics` endpoint
- Prometheus scrapes at regular intervals
- Data stored as time-series
- Queried via Prometheus API or dashboards

#### Example Metrics

```text
iscsi_targets 2
projected_images 10
deleted_images 3
```

#### Access Methods

- Prometheus API:
  ```bash
  curl http://prometheus:9090/api/v1/query?query=iscsi_targets
  ```

#### Advantages

- Industry-standard monitoring solution
- Historical data analysis
- Visualization support

#### Limitations

- More complex setup
- Overhead for simple use cases
- Not ideal for structured queries

---

## References

- Kubernetes DaemonSet Documentation: [https://kubernetes.io/docs/concepts/workloads/controllers/daemonset/](https://kubernetes.io/docs/concepts/workloads/controllers/daemonset/)

- Kubernetes Custom Resources: [https://kubernetes.io/docs/concepts/extend-kubernetes/api-extension/custom-resources/](https://kubernetes.io/docs/concepts/extend-kubernetes/api-extension/custom-resources/)

- Kubernetes API Overview: [https://kubernetes.io/docs/concepts/overview/kubernetes-api/](https://kubernetes.io/docs/concepts/overview/kubernetes-api/)

- Kubernetes Monitoring and Metrics: [https://kubernetes.io/docs/concepts/cluster-administration/system-metrics/](https://kubernetes.io/docs/concepts/cluster-administration/system-metrics/)

- Prometheus Documentation: [https://prometheus.io/docs/prometheus/latest/getting\_started/](https://prometheus.io/docs/prometheus/latest/getting_started/)

- Kubernetes Resource Monitoring: [https://kubernetes.io/docs/tasks/debug/debug-cluster/resource-usage-monitoring/](https://kubernetes.io/docs/tasks/debug/debug-cluster/resource-usage-monitoring/)

