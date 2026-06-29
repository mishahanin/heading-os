# Burst - Worked Examples

Consumed by: `.claude/skills/burst/SKILL.md` reference when modelling Phase 2 output format and the cost-incurring gate behaviour from Phase 1.4.

Last Updated: 2026-06-10

## Example A - text-only burst (no gate)

User: `/burst 3: opener for a LinkedIn post about sovereign DPI`

Response:

```
Varying: "opening hook for a LinkedIn post on sovereign deep packet intelligence"

## Variant 1 - operator-voice hook
Every nation we work with eventually asks the same question: who owns
the packet inspection layer that sees everything? The answer, today, is
almost always the same three foreign vendors. That is the problem.

## Variant 2 - data-led declaration
73% of national telco DPI deployments today run on foreign-owned probe
stacks. Of those, none publish what their telemetry pipelines do with
the data once it leaves the operator's tenancy. We changed that.

## Variant 3 - swing: contrarian provocation
"Sovereign DPI" is a marketing word. There is no such thing as a
sovereign packet inspector you bought from a vendor whose root CA is
not yours. Either you control the keys, the firmware build, and the
telemetry sink, or you do not.

My pick: Variant 2 - the data-led opener lands harder on the operator
audience that recognizes the 73% number from their own RFPs.

Reply "use variant N" to converge (next /burst will walk back to your choice), or any other message to continue normally.
```

## Example B - cost-incurring burst (gate fires)

User: `/burst 4: image of an Arctic research vessel at midnight sun`

Response:

```
Heads up: producing 4 variants of this would run 4 Nano Banana 2
generations at ~$0.04 each = ~$0.16 total. Reply "go" to proceed, or
revise the brief first.
```

User: `go`

Then variants are produced and saved per /flux-image conventions.
