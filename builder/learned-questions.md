# Learned questions

- Does every spoken `{{variable}}` have a producer that runs before that node, or is it explicitly supplied as a call input?
- Could wording required for a disclosure be mistaken by the judge for a claim that a write tool already succeeded?
- When a read returns several items, does every item reach exactly one brief-defined terminal disposition before closing, except when the brief says a transfer replaces item-level writes?
- When the brief defines calendar rules, is every customer-proposed date normalized and validated by deterministic code before any offer or write?
- Does code that reads an engine-run call's result look it up under the node-named flattened keys (not only the tool name or a response mapping), and was it tested against that exact key shape?
- When items carry different limits, does the agent check a customer-proposed value against each item's own limit and preserve it wherever it is valid?
- When an utterance contains multiple date signals, does deterministic code follow the brief-defined precedence or request clarification instead of silently discarding one?
- Can any write-tool hook silently change a value after the customer confirmed it, instead of rejecting it and requiring a new read-back?
- Does every conversation node where the customer can first answer a question (not only the main negotiation node) extract the values that deterministic checks read, and does each API tool's pre-hook rebuild a well-formed request when the model sends flat arguments, with terminal routing allowing one retry after a failed close?
- Does code that reads a model-filled tool argument look it up under the placeholder variable name used in the field's default (not only the API field name), with a fallback scan of all argument keys?
- When limits or attempts differ by item, does deterministic per-item state compute the next allowed action and permanently exclude items that already reached a terminal disposition?
- When code rebuilds an API result from the engine's flattened result keys (because a whole-body mapping did not land), does it turn numeric-index keys back into lists, and was it checked against a real call's variable table?
