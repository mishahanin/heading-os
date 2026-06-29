# Test Prompts: crm

## Should Trigger (positive)
Realistic, messy prompts that SHOULD activate this skill.

- "add contact -- met a guy named Daniel Okafor at TradeExpo, works for Nimbus Telecom, VP of network ops"
- "log a call with Sara, we talked about the PoV timeline and he's pushing for Q3"
- "who's on radar right now? anything going stale?"
- "find the guy from Meridian Mobile I talked to in February, can't remember his name"
- "update Sara's status to warm, we had a good call yesterday"
- "crm radar -- what needs attention this week"
- "log interaction with Sam, quick sync about ExampleTelco pricing, he's aligned"
- "when was the last time I talked to Leo Marsh?"
- "add a note to the ExampleTelco contact -- they're restructuring their procurement team"
- "who do we have at Atlas Telecom? list all contacts"

## Should NOT Trigger (negative)
Prompts that are similar but should NOT activate this skill.

- "add this person to Google Contacts" (google-contacts skill, not CRM)
- "draft an email to Sara about the timeline" (email-draft skill)
- "prepare me for the meeting with Nimbus Telecom" (meeting-prep skill)
- "find Karim's phone number in my contacts" (google-contacts lookup)
- "what do we know about Meridian Mobile?" (OSINT, not CRM record management)
