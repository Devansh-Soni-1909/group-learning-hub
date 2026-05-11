# HPC Cluster iSCSI SBPS Management Utility

## Overview

An **HPC (High-Performance Computing) cluster** is a collection of networked machines (nodes) that work together to perform large-scale computations in parallel. These clusters typically consist of:

- **Management Nodes (Master / Worker)** – Control and orchestration
- **Login Nodes** – User access points
- **Compute Nodes (Diskless)** – Execute workloads

All nodes are interconnected through a high-speed network to enable efficient communication and data sharing.

---

## Problem Statement

In diskless HPC environments, compute and login nodes require a mechanism to boot over the network. This is achieved using **iSCSI-based Boot Content Projection**.

---

## iSCSI SBPS

**iSCSI (Internet Small Computer Systems Interface)** is a block-level storage protocol that transmits SCSI commands over TCP/IP networks.

**iSCSI SBPS (Scalable Boot Projection Service)** enables:

- Remote booting of diskless nodes
- Projection of boot images such as:
  - Root Filesystem (rootfs)
  - Pre-Execution Environment (PXE/PE) images
- Centralized storage management for scalable clusters

---

## Project Objective

To develop a **robust CLI-based management utility** that:

- Runs on the **Master Node**
- Retrieves iSCSI SBPS-related data from all target nodes
- Eliminates the need for manual login into each node
- Provides centralized visibility into storage and boot configuration

---

## Key Features (Planned / Implemented)

- Cluster-wide iSCSI target discovery
- Automated iSCSI related data collection across nodes
- Lightweight and scriptable CLI interface
