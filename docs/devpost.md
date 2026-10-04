# AI Network Architect Copilot

**An AI writes the network design. Deterministic code proves it's correct.**

## Inspiration

Ask any chatbot to "design a network for a 50-person office" and you get
something that looks right: VLANs, subnets, a diagram, a wall of Cisco config.
Nobody can tell whether it actually works until it's deployed and something
breaks: two subnets overlap, the "isolated" guest Wi-Fi can reach the
payroll server, the backup internet line never takes over.

The usual fix is to ask a second AI to review the first one. We didn't want
that. Networks follow rules you can check with arithmetic and graph search,
so we split the job: the AI handles the one thing it's good at, turning
messy English into structured requirements, and plain code does everything
that has to be right.

## What it does

You describe a network in plain English, e.g. *"a 24/7 vet hospital with 90
staff, separate networks for clinical and front desk, isolated guest Wi-Fi,
no single point of failure"*. In under a minute you get:

- **What we understood**: the structured requirements, plus every
  assumption the AI made, shown so a human can check it ("90 users = 58
  full-time + 22 part-time + 10 visiting; remote staff excluded").
- **A complete design**: non-overlapping VLANs and subnets sized for every
  user, and a topology scaled to the headcount (access switches, access
  points, and redundant ISPs, routers, firewalls and core switches when
  asked for).
- **A validation report**: 12 checks, all plain code. 8 check the design
  (no overlapping subnets, private address ranges, enough addresses, every
  device reachable, redundancy actually present, guest traffic forced
  through the firewall). 4 audit the generated configs themselves (the guest
  ACL is really applied, gateways agree, no duplicate IPs, every route
  resolves).
- **Break it**: 12 one-click sabotages, such as two VLANs sharing a subnet,
  a cable around the firewall, or forgetting to apply the guest ACL. On a
  typical design each one turns exactly one check red, so you watch the
  validator catch the mistake a tired engineer makes at 2 a.m.
- **A failure simulator**: knock out any devices and it walks traffic hop by
  hop through the generated configs (ACLs, static routes, HSRP failover, IP
  SLA tracking, NAT and the return path) to show which networks still reach
  the internet and which can reach each other.
- **Cisco IOS-style configs** for every device, downloadable as a zip, a
  budgetary **bill of materials**, and a one-page **PDF of the diagram**.
- **Refine and ask**: change the design in plain English ("make it 150 users
  and add a pharmacy network") and see exactly what changed, or ask a
  question and get an answer that cites this design's actual devices and
  config lines.

## How we built it

- **Backend:** Python, FastAPI and Pydantic. One shared schema file is the
  contract between the two halves, which let us build in parallel from
  day one.
- **AI layer:** Claude (Sonnet 5.5, via the Anthropic API) does exactly three
  jobs: extract requirements, apply a plain-English change, and answer
  questions about a finished design. Structured outputs make the API return
  JSON matching our schema, which is then validated again, with retries and
  clean errors when the model or the API misbehaves.
- **Engine (no AI):** a generator (requirements → VLANs, addressing,
  topology), a validator, a config generator, a config auditor that parses
  the configs back and evaluates them, and a packet-walking simulator.
- **Dashboard:** plain HTML, CSS and JavaScript served by FastAPI, with no
  build step. The topology is hand-drawn SVG using standard network symbols.
- **Tests:** over 400 automated tests, including a check that every sabotage
  trips only its own check and that a healthy redundant network really uses
  its primary internet line.
- **Works offline:** built-in sample designs run the full engine with no AI
  call, so the demo survives bad Wi-Fi.

## Challenges we ran into

- **The checker needed checking.** Our own review found that the backup
  internet line was always in use: the firewalls test the primary line with
  a ping that the edge routers never address-translated, so the ping never
  got a reply. Every test passed, because the simulator skipped address
  translation for that ping. We fixed the config and taught the simulator to
  treat the ping like real traffic, so it can't hide that class of bug again.
- **AI output is messy in small ways that break real devices.** A
  description like "kennel cameras, smart locks and X-ray machine" became a
  52-character VLAN name, and Cisco's limit is 32. A missing organization
  name produced Wi-Fi network names like "-STAFF". We found these by reading
  the generated configs end to end, then fixed them at the source in the
  prompts, with a fallback in code for the missing name.
- **One mistake, one red check.** For the demo to be convincing, each
  sabotage had to break exactly one check, not set off a cascade. Getting
  the validator's checks to stay independent took real care.

## Accomplishments that we're proud of

- The validation is real: arithmetic, graph search and a config parser, not
  a model's opinion.
- The simulator walks packets through the configs we generate, so
  "redundant" means a tested failover path, not a second box on a diagram.
- A paragraph of English becomes a validated, costed, deployable-looking
  design in under a minute, and it still works with the internet unplugged.

## What we learned

- Use the AI where language is ambiguous and code where correctness
  matters, and make the AI show its assumptions so a person can check them.
- Tests that share a blind spot with the code agree with each other and
  prove nothing. Our worst bug passed every test until we changed what the
  simulator modeled.
- A shared schema agreed on day one let two people build a frontend and an
  engine in parallel with almost no merge conflicts.

## What's next

- **Boot the configs for real** in a network emulator (containerlab), to
  prove they run, not just that they're consistent.
- **Multi-vendor output**: Juniper, Aruba and Ubiquiti alongside Cisco.
- **Existing networks**: import a current setup and propose changes to it,
  which is what most real network projects are.
- **Compliance packs**: extra checks for standards such as PCI-DSS network
  segmentation.

## Limitations

The configs are Cisco IOS-*style* and illustrative; they haven't been booted
on real hardware yet. Bill-of-materials prices are budgetary estimates, not
quotes.
