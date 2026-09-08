# Disposable workflow trial

This tiny local text toolkit is the authorized Lockin AI setup trial. It does
not read live bot data, modify the relay, or alter another project.

The integration owner owns this README, `cli.py`, and `test_integration.py`.
Feature plan owners own only their named `openspec/changes/trial-*/` directory.
Builders read approved plans and own only their assigned module and test file.

## Shared contract

- Greeting: `greeting.py` exports `greet(name: str) -> str`. Preserve case and
  trim outside whitespace; an empty/whitespace name uses `friend`.
  Output exactly `Hello, <name>!`.
- Count: `counter.py` exports `count_words(text: str) -> int`. Count whitespace
  separated tokens; punctuation stays part of its token. Empty input is zero.
  Do not normalize the input or change the greeting decision.
- Integration: `cli.py --name NAME --text TEXT` prints one JSON object with
  `greeting` and `word_count`, invoking both real modules. Standard library only.
- Both feature slices are independent after this committed foundation. The
  lead integrates both commits and checks CLI output including blank inputs.

Only this disposable trial's two tiny feature builds are preauthorized by the
setup request. Real product features still require their own approved scope.
