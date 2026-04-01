This method is used to collect data from multiple worker nodes in a simple way.
All worker nodes share a common folder, and each node writes its data into that folder. The master node reads this data and displays the result.

How it Works:
1. A shared folder is created on the master node.
2. All worker nodes connect to this common folder.
3. Each worker node writes its data into a file.
4. The master node reads all files.
5. The final output is displayed.


3. Required Data
List of Workers Configured as iSCSI Targets
Each worker node has:
Node name
IP address
iSCSI target name

Example:
Worker1, Worker2, Worker3


Number of Projected Images per Worker Node (T)
T = number of active images (LUNs) on that node

Example:
- Worker1 -> 3
- Worker2 -> 2

Total Number of Images Projected

Total=sum of images from all nodes
Example:
Total = 3 + 2 = 5


Number of Deleted Images per Worker Node

- Number of images removed from each node

Example:
- Worker1 -> 1 deleted image
- Worker2 -> 0 deleted images


Advantages
 Easy to use
 No complex setup
 Good for small systems

Disadvantages
Not scalable-Works only for small systems. Difficult to manage with many nodes

References:
https://docs.redhat.com/en/documentation/red_hat_openstack_services_on_openshift/18.0/html/planning_your_deployment/assembly_planning-storage#ref_storage-planning-shared-file-systems_planning
https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/6/html/storage_administration_guide/ch-nfs
https://linux.die.net/man/5/nfs


