from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, DEAD_DISPATCHER
from ryu.controller.handler import set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet, ether_types
from ryu.topology import event
from ryu.topology.api import get_switch, get_link, get_host
from ryu.app.wsgi import ControllerBase, WSGIApplication, route
from webob import Response
import json
import logging

api_instance_name = 'api_app'

class NetworkController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    _CONTEXTS = {'wsgi': WSGIApplication}
    
    def __init__(self, *args, **kwargs):
        super(NetworkController, self).__init__(*args, **kwargs)
        
        # Topology storage
        self.topology = {
            'switches': {},
            'links': [],
            'hosts': {},
            'version': 0
        }
        
        self.mac_to_port = {} # MAC to port mapping for each switch
        
        self.datapaths = {} # Track datapaths
        
        # Register REST API
        wsgi = kwargs['wsgi']
        wsgi.register(
            NetworkAPI,
            {api_instance_name: self}
        )
    
    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def state_change_handler(self, ev): # Handle datapath state changes
        datapath = ev.datapath
        
        if ev.state == MAIN_DISPATCHER: # Negotiation between RYU and OF Switch must be completed
            if datapath.id not in self.datapaths:
                self.logger.info(f"Switch DPID {datapath.id} CONNECTED")
                self.datapaths[datapath.id] = datapath
        elif ev.state == DEAD_DISPATCHER:
            if datapath.id in self.datapaths:
                self.logger.warning(f"Switch DPID {datapath.id} DISCONNECTED")
                del self.datapaths[datapath.id]
    
    # Code source: https://osrg.github.io/ryu-book/en/html/switching_hub.html
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER) # Waiting to receive SwitchFeatures message
    def switch_features_handler(self, ev): # Handle OF switch connection
        try: # RYU gets this reply from a previously sent request to the switch
            datapath = ev.msg.datapath 
            ofproto = datapath.ofproto
            parser = datapath.ofproto_parser
            
            self.logger.info(f"Configuring switch DPID {datapath.id}")
            
            # Install table-miss flow entry
            match = parser.OFPMatch()
            actions = [parser.OFPActionOutput(
                ofproto.OFPP_CONTROLLER,
                ofproto.OFPCML_NO_BUFFER
            )]
            self.add_flow(datapath, 0, match, actions)
            
            self.logger.info(f"Switch DPID {datapath.id} configured successfully")
            
            # Trigger topology update
            self.update_topology()
        except Exception as e:
            self.logger.error(f"Error in switch_features_handler: {e}")
            self.logger.exception(e)
    
    def add_flow(self, datapath, priority, match, actions, buffer_id=None): # Add a flow entry to the switch
        try:
            ofproto = datapath.ofproto
            parser = datapath.ofproto_parser
            
            inst = [parser.OFPInstructionActions(
                ofproto.OFPIT_APPLY_ACTIONS, actions
            )]
            
            if buffer_id:
                mod = parser.OFPFlowMod(
                    datapath=datapath, buffer_id=buffer_id,
                    priority=priority, match=match, instructions=inst
                )
            else:
                mod = parser.OFPFlowMod(
                    datapath=datapath, priority=priority,
                    match=match, instructions=inst
                )
            
            datapath.send_msg(mod)
        except Exception as e:
            self.logger.error(f"Error adding flow: {e}")
    
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev): # Handle packet-in messages (in means into the ryu controller)
        try:
            msg = ev.msg
            datapath = msg.datapath
            ofproto = datapath.ofproto
            parser = datapath.ofproto_parser
            in_port = msg.match['in_port']
            
            pkt = packet.Packet(msg.data)
            eth = pkt.get_protocols(ethernet.ethernet)[0]
            
            if eth.ethertype == ether_types.ETH_TYPE_LLDP:
                # Don't process LLDP packets
                return
            
            dst = eth.dst
            src = eth.src
            dpid = datapath.id
            
            self.mac_to_port.setdefault(dpid, {})
            
            # Learn MAC address
            self.mac_to_port[dpid][src] = in_port
            
            if dst in self.mac_to_port[dpid]:
                out_port = self.mac_to_port[dpid][dst]
            else:
                out_port = ofproto.OFPP_FLOOD
            
            actions = [parser.OFPActionOutput(out_port)]
            
            # Install flow to avoid packet_in next time
            if out_port != ofproto.OFPP_FLOOD:
                match = parser.OFPMatch(in_port=in_port, eth_dst=dst, eth_src=src)
                if msg.buffer_id != ofproto.OFP_NO_BUFFER:
                    self.add_flow(datapath, 1, match, actions, msg.buffer_id)
                    return
                else:
                    self.add_flow(datapath, 1, match, actions)
            
            data = None
            if msg.buffer_id == ofproto.OFP_NO_BUFFER:
                data = msg.data
            
            out = parser.OFPPacketOut(
                datapath=datapath, buffer_id=msg.buffer_id,
                in_port=in_port, actions=actions, data=data
            )
            datapath.send_msg(out)
        except Exception as e:
            self.logger.error(f"Error in packet_in_handler: {e}")
    
    @set_ev_cls(event.EventSwitchEnter)
    def switch_enter_handler(self, ev): # Handle switch addition
        try:
            switch = ev.switch
            self.logger.info(f"Topology: Switch {switch.dp.id} ENTERED")
            self.update_topology()
        except Exception as e:
            self.logger.error(f"Error in switch_enter_handler: {e}")
    
    @set_ev_cls(event.EventSwitchLeave)
    def switch_leave_handler(self, ev): # Handle switch removal
        try:
            switch = ev.switch
            self.logger.warning(f"Topology: Switch {switch.dp.id} LEFT")
            self.update_topology()
        except Exception as e:
            self.logger.error(f"Error in switch_leave_handler: {e}")
    
    @set_ev_cls(event.EventLinkAdd)
    def link_add_handler(self, ev): # Handle link addition
        try:
            link = ev.link
            self.logger.info(f"Topology: Link ADDED s{link.src.dpid}:{link.src.port_no} -> s{link.dst.dpid}:{link.dst.port_no}")
            self.update_topology()
        except Exception as e:
            self.logger.error(f"Error in link_add_handler: {e}")
    
    @set_ev_cls(event.EventLinkDelete)
    def link_delete_handler(self, ev): # Handle link deletion
        try:
            link = ev.link
            self.logger.warning(f"Topology: Link DELETED s{link.src.dpid}:{link.src.port_no} -> s{link.dst.dpid}:{link.dst.port_no}")
            self.update_topology()
        except Exception as e:
            self.logger.error(f"Error in link_delete_handler: {e}")
    
    @set_ev_cls(event.EventHostAdd)
    def host_add_handler(self, ev): # Handle host addition
        try:
            host = ev.host
            self.logger.info(f"Topology: Host ADDED {host.mac} at s{host.port.dpid}:{host.port.port_no}")
            self.update_topology()
        except Exception as e:
            self.logger.error(f"Error in host_add_handler: {e}")
    
    def update_topology(self): # Update topology information
        try:
            switch_list = get_switch(self, None)
            switches = {}
            for switch in switch_list:
                dpid = switch.dp.id
                switches[str(dpid)] = {
                    'dpid': dpid,
                    'ports': [port.port_no for port in switch.ports if port.port_no < 65535]
                }
            
            # Get all links
            links_list = get_link(self, None)
            links = []
            for link in links_list:
                links.append({
                    'src_dpid': link.src.dpid,
                    'src_port': link.src.port_no,
                    'dst_dpid': link.dst.dpid,
                    'dst_port': link.dst.port_no
                })
            
            # Get all hosts
            hosts_list = get_host(self, None)
            hosts = {}
            for host in hosts_list:
                hosts[host.mac] = {
                    'mac': host.mac,
                    'ipv4': host.ipv4[0] if host.ipv4 else None,
                    'ipv6': host.ipv6[0] if host.ipv6 else None,
                    'port': host.port.port_no,
                    'dpid': host.port.dpid
                }
            
            # Update topology
            old_version = self.topology['version']
            self.topology = {
                'switches': switches,
                'links': links,
                'hosts': hosts,
                'version': old_version + 1
            }
            
            self.logger.info(f"Topology updated to version {self.topology['version']} - Switches: {len(switches)}, Links: {len(links)}, Hosts: {len(hosts)}")
        except Exception as e:
            self.logger.error(f"Error updating topology: {e}")
            self.logger.exception(e)

class NetworkAPI(ControllerBase): # REST API for topology exposure
    def __init__(self, req, link, data, **config):
        super(NetworkAPI, self).__init__(req, link, data, **config)
        self.controller = data[api_instance_name]
    
    @route('topology', '/api/topology', methods=['GET'])
    def get_topology(self, req, **kwargs):
        body = json.dumps(self.controller.topology, indent=2)
        return Response(
            content_type='application/json',
            body=body.encode('utf-8')
        )
    
    @route('switches', '/api/switches', methods=['GET'])
    def get_switches(self, req, **kwargs):
        body = json.dumps(self.controller.topology['switches'], indent=2)
        return Response(
            content_type='application/json',
            body=body.encode('utf-8')
        )
    
    @route('links', '/api/links', methods=['GET'])
    def get_links(self, req, **kwargs):
        body = json.dumps(self.controller.topology['links'], indent=2)
        return Response(
            content_type='application/json',
            body=body.encode('utf-8')
        )
    
    @route('hosts', '/api/hosts', methods=['GET'])
    def get_hosts(self, req, **kwargs):
        body = json.dumps(self.controller.topology['hosts'], indent=2)
        return Response(
            content_type='application/json',
            body=body.encode('utf-8')
        )

    @route('version', '/api/version', methods=['GET'])
    def get_version(self, req, **kwargs):
        version_info = {
            'version': self.controller.topology['version']
        }
        body = json.dumps(version_info, indent=2)
        return Response(
            content_type='application/json',
            body=body.encode('utf-8')
        )