# SDN Digital Twin

A Software-Defined Networking (SDN) digital twin implementation using Mininet and Ryu controller. This project creates a virtual replica of a physical SDN network that synchronizes in real-time with the original network topology.

## Overview

This project implements a digital twin architecture for SDN networks, enabling network operators to:
- Mirror a physical SDN topology in a virtual environment
- Monitor real-time topology changes and synchronize them with the twin
- Test network configurations safely before deploying to production
- Analyze network behavior in an isolated environment

## Architecture

The project consists of three main components:

- **`controller.py`**: Ryu SDN controller that manages the physical network using OpenFlow 1.3 protocol. Provides REST API for topology information.
- **`net.py`**: Mininet network topology definition
- **`twin.py`**: Digital twin implementation that fetches topology from the physical network controller and replicates it in a separate Mininet instance

## Requirements (IMPORTANT)

It is **highly recommended** to run this project in a VM because of the priviledge required by ComNetSemu and Docker container. [ComNetsEmu](https://git.comnets.net/public-repo/comnetsemu) is a testbed and network emulator which extends Mininet to support better emulation of versatile Computing In The Network (COIN) applications. ComNetSemu provides a Vagrant file which spins up the VM, but also an installer script in the */util* folder (run the latter in a VM with image [Ubuntu 20.04 LTS](https://www.releases.ubuntu.com/focal/) is supported ). Between the two, I personally found easier the **second approach**.

Once you are within the VM, you need following packages: 
- Git (sudo apt install git)
- Python 3.8.10 (sudo apt install python3.8.10) [should be preinstalled with Ubuntu]
- Pip (sudo apt install python3-pip)
- Ansible (sudo apt install ansible)
- [Mininet](http://mininet.org/) (installed with ComNetSemu)
- [Ryu SDN Controller](https://ryu-sdn.org/) (installed with ComNetSemu)
- Open vSwitch (installed with ComNetSemu)

NOTE: you do not need to install any of this if you use the Vagrant file for ComNetSemu because the provision scripts takes care of the packages cited above.

## Project execution (Follow carefully, order matters)
Before executing any shell script, open 4 terminal windows (ctrl+shit+t).

### 1. Launch the Physical Network

In terminal 1, start the Mininet physical network:
```bash
sudo python3 net.py
```

This crates the network specified (can customize it as you want).

### 2. Start the Physical Network Controller

In terminal 2, start the Ryu controller for the physical network:
```bash
ryu-manager --observe-links controller.py
```

Once started, go back to terminal 1 and wait for Mininet CLI to pop up.
When CLI is availabe, means that the switches of the topology have been configured and the network is ready to use.

The controller will:
- Listen on port 6633 for OpenFlow switches (default port)
- Expose REST API on port 8080 for topology queries (--wsapi-port 8080)

### 3. Start the Digital Twin Controller

In terminal 3, start the Ryu controller for the digital twin:
```bash
ryu-manager --observe-links --wsapi-port 8081 --ofp-tcp-listen-port 6634 controller.py
```

NOTE: the same controller as the original network is used, but using different ports.

### 4. Launch the Digital Twin

In terminal 4, create the digital twin that mirrors the physical network:
```bash
sudo python3 twin.py --sync
```
The **--sync** flag runs a backgroud process that keeps the twin in sync with the original network.
Once again wait in terminal 4 for the Mininet CLI to show up.

The digital twin will:
- Fetch topology from the physical controller (localhost:8080)
- Replicate the topology in a separate Mininet instance
- Continuously synchronize with the physical network
- Connect to its own controller on port 6634

NOTE: If you look the logs of the controller, you will see that no host is present. Just run *pingall* in Mininet CLI to discover the host (look at the controller's logs for confirmation).

### Contollers count explanation
There are difference between what you expect and what it actually is.

#### Link count
If you look at the controllers logs, you will see only 4 links in the topology. For the provided topology in net.py, one can see that there are 5 links, but actually there are 10. This is because links are bi-directional. But why the logs say only 4? **--observe-links** only discovers switch-to-switch links using LLDP (Link Layer Discovery Protocol), meaning only 2 bi-directional links between switches. The remaining 6, host-to-switch and vice versa, are not discovered with LLDP. Hosts are discovered separately via *packet-in* events when they send traffic.

#### Host count
If you look the logs of the controller and host count is 0, just run *pingall* in Mininet CLI to discover the host (look at the controller's logs for confirmation).

## Testing

Once all components are running, you can test the setup:

### Test Physical Network
In the Mininet CLI (terminal 2):
```bash
mininet> pingall                    # Test connectivity between all hosts
mininet> net                        # Test bandwidth
```

### Test Digital Twin
In the digital twin CLI (terminal 4):
```bash
mininet> pingall                    # Verify twin mirrors physical network behavior
mininet> net                        # Display twin topology
```

### Test link sync
In the Mininet CLI (terminal 2):
```bash
mininet> link s1 s2 down            # Disable any link. Wait for twin to detect the change (10s max)
```
Now, in the digital twin CLI (terminal 4):
```bash
mininet> twin_h1 ping -c1 twin_h2   # Test link. Packet should not go through
```
Back to Mininet CLI (terminal 2):
```bash
mininet> link s1 s2 up              # Enable the link. Wait for twin to detect the change (10s max)
```
Finally, in the digital twin CLI (terminal 4):
```bash
mininet> twin_h1 ping -c1 twin_h2   # Test link. Now the packet should go through
```

### Verify Synchronization
```bash
# Query physical network topology
curl http://localhost:8080/api/topology

# Query digital twin topology
curl http://localhost:8081/api/topology
```

Both should return identical topology structures (switches, links, hosts).

## Limitations

Due to Mininet's architecture constraints, the digital twin has the following limitations:

### Supported Dynamic Updates
- **Link changes**: Links can be added/removed dynamically and synchronized in real-time
- **Host addition**: New hosts can be added dynamically to the twin network (py interpreter in Mininet CLI might not work properly)

### UNsupported Dynamic Updates
- **Switch addition/removal**: Switches **cannot** be added or removed dynamically in Mininet once the network is running
- **Host removal**: Hosts **cannot** be removed dynamically (detection only - the twin will log the change but the host remains)

### Workaround for Switch Topology Changes

If switches are added or removed from the physical network:

1. The twin will detect the change and display a warning
2. Exit the digital twin CLI (type `exit`)

This limitation is inherent to Mininet's design, which requires the network topology to be defined at initialization time.

## How It Works

1. The **physical network** runs in Mininet with switches controlled by a Ryu controller
2. The Ryu controller tracks topology (switches, links, hosts) via OpenFlow and exposes it via REST API
3. The **digital twin** periodically fetches the topology from the physical controller's API
4. The twin dynamically creates/updates a Mininet replica matching the physical topology
5. Both networks operate independently but maintain synchronized topologies

## Use Cases

- **Testing**: Validate configuration changes in the twin before applying to production
- **Training**: Learn SDN concepts without affecting real infrastructure
- **Analysis**: Monitor and analyze network behavior in isolation
- **Development**: Develop and test SDN applications safely
