# SBPS iSCSI Metrics Collection 

## 1. Objective

Fetch the following metrics from the master node:

- List of iSCSI target worker nodes
- Active (projected) images per node
- Total projected images (cluster-wide)
- Deleted images per node

---

## 2. Prerequisites

- Kubernetes cluster (1 master + 4 workers)
- SLES installed on all nodes
- kubectl configured on master
- SSH access (optional)
- Directory structure created:

sudo mkdir -p /var/lib/sbps/active
sudo mkdir -p /var/lib/sbps/deleted

---

## 3. METHOD : DaemonSet + REST API 

---

### 3.1 Deploy Node Agent

#### Step 1: Create Agent Code (on master)

cat > main.py <<EOF
from fastapi import FastAPI
import os
import subprocess

app = FastAPI()
BASE_PATH = "/var/lib/sbps"

def count_files(path):
    return len(os.listdir(path)) if os.path.exists(path) else 0

def is_iscsi_target():
    return subprocess.getoutput("systemctl is-active target") == "active"

@app.get("/metrics")
def metrics():
    return {
        "node": os.uname().nodename,
        "iscsi_target": is_iscsi_target(),
        "active_images": count_files(BASE_PATH + "/active"),
        "deleted_images": count_files(BASE_PATH + "/deleted")
    }
EOF

---

#### Step 2: Create Docker Image

cat > Dockerfile <<EOF
FROM python:3.10
WORKDIR /app
COPY . .
RUN pip install fastapi uvicorn
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
EOF

docker build -t sbps-agent:latest .

---

### 3.2 Deploy DaemonSet

cat > daemonset.yaml <<EOF
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
      - name: agent
        image: sbps-agent:latest
        ports:
        - containerPort: 8000
        volumeMounts:
        - name: sbps-data
          mountPath: /var/lib/sbps
      volumes:
      - name: sbps-data
        hostPath:
          path: /var/lib/sbps
EOF

kubectl apply -f daemonset.yaml

---

### 3.3 Fetch Metrics from Master

#### Step 1: Get Node IPs

kubectl get nodes -o wide

---

#### Step 2: Call Each Node

curl http://worker1:8000/metrics
curl http://worker2:8000/metrics

---

#### Step 3: Aggregate (Python)

cat > aggregator.py <<EOF
import requests

nodes = ["worker1", "worker2", "worker3", "worker4"]

total_active = 0
total_deleted = 0
iscsi_targets = []

for node in nodes:
    try:
        res = requests.get(f"http://{node}:8000/metrics", timeout=2).json()
        total_active += res["active_images"]
        total_deleted += res["deleted_images"]

        if res["iscsi_target"]:
            iscsi_targets.append(node)

        print(node, res)
    except:
        print(node, "unreachable")

print("Total Active:", total_active)
print("Total Deleted:", total_deleted)
print("iSCSI Targets:", iscsi_targets)
EOF

python3 aggregator.py

---
