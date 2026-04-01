\# SBPS iSCSI Metrics Collection System

\---

\## 1. Objective

Fetch the following metrics from the master node:

\- List of iSCSI target worker nodes

\- Active (projected) images per worker node

\- Total projected images (cluster-wide)

\- Deleted images per worker node

\---

\## 2. System Overview

This system monitors distributed storage (iSCSI + SBPS images) in a Kubernetes cluster.

\### Cluster Configuration:

\- 1 Master Node

\- 4 Worker Nodes

\- OS: SLES (SUSE Linux Enterprise Server)

\- Orchestration: Kubernetes

\---

\## 3. Data Model

Each worker node maintains:

\`\`\`bash

/var/lib/sbps/

├── active/ # Active images

└── deleted/ # Deleted images

4\. Prerequisites

Kubernetes cluster running

kubectl configured on master

Docker installed

Network connectivity across nodes

Create directories on all nodes:

sudo mkdir -p /var/lib/sbps/active

sudo mkdir -p /var/lib/sbps/deleted

5\. Architecture: DaemonSet + REST API

Each node runs an agent exposing metrics. Master collects and aggregates.

Worker Node → Agent (/metrics API)

Master Node → Aggregator → Final Output

6\. Implementation

6.1 Create Node Agent

Step 1: Agent Code

cat > main.py <

from fastapi import FastAPI

import os

import subprocess

app = FastAPI()

BASE\_PATH = "/var/lib/sbps"

def count\_files(path):

return len(os.listdir(path)) if os.path.exists(path) else 0

def is\_iscsi\_target():

return subprocess.getoutput("systemctl is-active target") == "active"

@app.get("/metrics")

def metrics():

return {

"node": os.uname().nodename,

"iscsi\_target": is\_iscsi\_target(),

"active\_images": count\_files(BASE\_PATH + "/active"),

"deleted\_images": count\_files(BASE\_PATH + "/deleted")

}

EOF

Step 2: Dockerfile

cat > Dockerfile <

FROM python:3.10

WORKDIR /app

COPY . .

RUN pip install fastapi uvicorn

CMD \["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"\]

EOF

Step 3: Build Image

docker build -t sbps-agent:latest .

6.2 Deploy DaemonSet

Step 1: YAML

cat > daemonset.yaml <

apiVersion: apps/v1

kind: DaemonSet

metadata:

name: sbps-agent

spec:

selector:

matchLabels:

app: sbps-agent

template:

metadata:

labels:

app: sbps-agent

spec:

containers:

\- name: agent

image: sbps-agent:latest

ports:

\- containerPort: 8000

volumeMounts:

\- name: sbps-data

mountPath: /var/lib/sbps

volumes:

\- name: sbps-data

hostPath:

path: /var/lib/sbps

EOF

Step 2: Apply

kubectl apply -f daemonset.yaml

Step 3: Verify

kubectl get pods -o wide

6.3 Fetch Metrics from Master

Step 1: Get Nodes

kubectl get nodes -o wide

Step 2: Call APIs

curl http://worker1:8000/metrics

curl http://worker2:8000/metrics

curl http://worker3:8000/metrics

curl http://worker4:8000/metrics

Step 3: Aggregator Script

cat > aggregator.py <

import requests

nodes = \["worker1", "worker2", "worker3", "worker4"\]

total\_active = 0

total\_deleted = 0

iscsi\_targets = \[\]

for node in nodes:

try:

res = requests.get(f"http://{node}:8000/metrics", timeout=2).json()

total\_active += res\["active\_images"\]

total\_deleted += res\["deleted\_images"\]

if res\["iscsi\_target"\]:

iscsi\_targets.append(node)

print(f"{node}: {res}")

except:

print(f"{node} unreachable")

print("\\\\n===== FINAL OUTPUT =====")

print("Total Active Images:", total\_active)

print("Total Deleted Images:", total\_deleted)

print("iSCSI Targets:", iscsi\_targets)

EOF

Step 4: Run

python3 aggregator.py

7\. Expected Output

{

"iscsi\_targets": \["worker1", "worker2"\],

"total\_active\_images": 5,

"total\_deleted\_images": 2

}

8\. Metric Logic

MetricCommand

iSCSI Targetsystemctl is-active target

Active Imagesls /var/lib/sbps/active

Deleted Imagesls /var/lib/sbps/deleted

Total ImagesSum across nodes

9\. Validation

kubectl get nodes

kubectl get pods

systemctl status target

ls /var/lib/sbps/active

10\. References

https://kubernetes.io/docs/

https://prometheus.io/docs/

https://linux-iscsi.org/

https://documentation.suse.com/

https://fastapi.tiangolo.com/

11\. Conclusion

This implementation uses:

DaemonSet for node-level agents

REST API for communication

Master aggregation for final metrics

It is scalable, reliable, and aligned with enterprise-grade system design.
