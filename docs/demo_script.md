# Demo script (about 4 minutes)

The pitch in one line: **an AI writes the network design, and then
deterministic code proves it's correct, instead of a second AI guessing.**

## Before you go on

- `uvicorn pipeline.api.main:app`, then open http://localhost:8000/ and do one
  full run so the first request isn't the cold one.
- No Wi-Fi or API key? The **sample design** buttons under the main form run
  the real engine with no AI call. Steps 3–6 work exactly the same.

## 1. Plain English in (30s)

Paste this, or click **24/7 vet hospital, fully redundant** if the network
is shaky:

> Northwind Veterinary Hospital: 58 full-time staff, 22 part-time vet techs
> and 10 visiting specialists, plus 6 billing people who work from home. We
> need separate networks for clinical, the front desk, and our kennel
> cameras, smart locks and X-ray machine. Guest Wi-Fi for the waiting room
> that can't touch anything else. We're a 24/7 emergency hospital, so no
> single internet outage or switch failure can take us down. Use
> 10.50.0.0/16. Front desk phones need voice QoS, and exam room ports need
> 802.1X.

It never says a headcount, "redundant", or "IoT" on purpose: the AI has to
work those out, which is what step 2 shows.

## 2. What we understood (20s)

Point at **Assumptions**: the AI shows its working so a human can check it,
not trust it blindly:
- **Headcount math**: 58 + 22 + 10 = 90, remote billing staff excluded.
- **Redundancy inferred** from "24/7" and "no single ... failure".
- **IoT segment**: cameras, locks and the X-ray machine grouped together.

The QoS and 802.1X asks show up under notes: things it heard but the engine
doesn't design for yet, said out loud instead of silently dropped.

If it ever counts the remote staff (96 users), that's a bonus: point at the
assumption, then in step 6 type *"don't count the remote billing staff"*.

## 3. The proof: validation (40s)

All checks green. Two layers:
- **Design checks**: no overlapping subnets, enough addresses, connected,
  redundant.
- **Config audit**: it re-reads the Cisco configs it generated and, for
  example, evaluates the guest ACL rule by rule.

*"Every check is plain code, not another model's opinion."*

## 4. Break it (40s)

Under **Break the configs**, click **Forget to apply the guest ACL**. The
design still looks perfect, but exactly one check goes red:
`guest_isolation_enforced`. That's the mistake a human makes at 2 a.m., and
it gets caught before anything is deployed. Click **Restore**.

## 5. Simulate failures (60s), the strongest moment

In **Simulate**, the badge reads **No single point of failure**. Click
**ISP Circuit A**, **Edge Router 1**, **Perimeter Firewall 1** and **Core
Switch 1**. With one device down in every layer, every VLAN still reaches
the internet. Click the staff → internet cell, scroll up, and the topology
shows the surviving path through the backup side.

Then click a red ✗ in the **guest** row: "denied by ACL GUEST-ISOLATION on
core1". The guest network really can't reach the clinic.

## 6. Change it and ask it (40s)

- Refine box: *"we're opening a second wing: make it 150 users and add a
  separate network for the pharmacy"*. It redesigns, re-proves it, and lists
  exactly what changed.
- Ask: *"What happens if the primary internet connection fails?"* The answer
  cites this design's actual devices and config lines.

## 7. Close (10s)

**Download all configs (.zip)** and the **Parts list**: from a paragraph
to a validated design and a shopping list in under a minute.

## If something goes wrong

- The AI call errors or is slow: use a sample design button.
- Someone asks "is the simulator real?": yes. It walks packets through the
  generated configs: ACLs, static routes, HSRP failover, IP SLA tracking,
  NAT and the return path.
- Someone asks about limits: configs are Cisco IOS-*style* and illustrative;
  the parts list gives specs and example models, not a quote.
