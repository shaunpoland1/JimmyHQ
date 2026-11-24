# Models

`server/jimmyhq/models/` is the one place JimmyHQ talks to language models. The owner
sets up providers and maps roles to models; every other module asks for a role and gets
an answer and a receipt. Local first: no prompt goes to a hosted model unless the owner
has put one in a role or picked it by name, and when a fallback answers, the reply says so.

## Calling a model from another module

```python
from jimmyhq.models import ModelError, brain

reply = await brain(core).chat(
    [{"role": "user", "content": "Summarise this note: ..."}],
    role="cheap", actor=who.actor, caller="notes.summary",
)
reply.text, reply.provider_id, reply.model, reply.fallback_from, reply.is_local
```

- `brain(core).stream(...)` takes the same arguments, `tools` included, and yields text
  pieces, then one final `Reply`.
- `brain(core).chat_sync(...)` is for worker jobs and other code that is not async.
- `local_only=True` means only providers marked local may answer. `no_fallback=True`
  means fail rather than try the next model. `keep_text=False` records token counts and
  nothing else, for private callers.
- `json_schema={...}` asks for JSON. `reply.data` is the parsed value. A reply that is
  not valid JSON raises. The schema itself is not checked against the value.
- Every failure is a `ModelError` with a `kind`, a sentence in `message`, and
  `fallback_ok`. A `truncated` one also carries `partial`: the text that had arrived when
  the answer ran out of room, whole or streamed, wherever the provider returns it. It is
  for the caller only and is never logged or put on a receipt.
- Read the `local` field of a `Reply` or `Provider` as `.is_local`. The public-safety
  test reads a dotted name ending in "local" as a private host name.

A role nobody has set uses the default role's models. The embeddings role never does.

## Tools and images

```python
tools = [{"name": "open_page", "description": "Open a web page and return its text.",
          "parameters": {"type": "object", "properties": {"url": {"type": "string"}},
                         "required": ["url"]}}]
conversation = [{"role": "user", "content": [
    {"type": "text", "text": "What does this page link to?"},
    {"type": "image", "media_type": "image/png", "data": screenshot_base64}]}]

reply = await brain(core).chat(conversation, tools=tools, role="smart",
                               actor=who.actor, caller="agent.step")
if reply.tool_calls:                   # each a ToolCall(id, name, arguments, signature)
    results = [{"type": "tool_result", "tool_call_id": call.id,
                "content": run(call.name, call.arguments)} for call in reply.tool_calls]
    conversation += [reply.as_message(), {"role": "user", "content": results}]
    reply = await brain(core).chat(conversation, tools=tools, role="smart",
                                   actor=who.actor, caller="agent.step")
```

- `tools=[{"name", "description", "parameters"}]`, with `parameters` a JSON Schema of type
  `object`. `tool_choice` is `"auto"` (the default), `"required"`, `"none"` or
  `{"name": ...}`. `chat`, `chat_sync` and `stream` all take them.
  `tools` and `json_schema` cannot be used in one call.
- **Streaming with tools.** `stream(..., tools=...)` yields the model's text as it
  arrives and then the final `Reply`, whose `tool_calls` are complete and checked exactly
  as `chat`'s are. Nothing is yielded for a tool call while it is still arriving. Which
  provider types really stream such a call:

  | Type | With tools, `stream` |
  |---|---|
  | `openai`, `openai_compatible` | Streams. Calls are put together from `delta.tool_calls` pieces by `index`; a server that sends each call whole, or leaves `index` out, is read too. |
  | `anthropic` | Streams. Content blocks are rebuilt from the stream (`input_json_delta`, `thinking_delta`, `signature_delta`), so thinking blocks are passed back as they came, as with `chat`. |
  | `google` | Streams. A `functionCall` part arrives whole in one chunk and is collected as it passes. |
  | `bedrock` | Does not. `stream` makes an ordinary `converse` call instead and yields its text in one piece before the `Reply`; the receipt's `streamed` is false. |

  An adapter says which it is with `streams_tools`. A stream that offers no tools is
  sent and read exactly as before.
- `reply.tool_calls` is a tuple of `ToolCall`. `arguments` is always a parsed JSON object;
  it is not checked against the tool's schema, so check it before acting. `reply.text` is
  whatever the model said alongside. A call whose arguments cannot be read, or that names
  a tool that was not offered, raises `bad_tool_call`.
- To continue, append `reply.as_message()` and then one user message with a
  `{"type": "tool_result", "tool_call_id", "content", "is_error"}` part for **every** call,
  before any other part, and pass the same `tools` again. `content` is text, or a list of
  text and image parts. `as_message()` carries each call's `signature`: an opaque value
  (a model's sealed reasoning, where it has any) that the same provider type needs back.
  It is never sent to a different provider type, and never kept.
- An image part is `{"type": "image", "media_type", "data"}` with `image/png`,
  `image/jpeg` or `image/webp` and base64 data, in a user message or inside a tool result.
  It is refused before anything is sent if it is over `models.max_image_bytes` (Settings,
  Models), is not valid base64, or is not the kind of file it says. `stream` takes images.
- **Images are never stored.** The receipt carries counts only (`tools`, `tool_calls`,
  `images`, `image_bytes`, present when used). With `keep_text`, the Vault copy of the
  prompt has each image replaced by its type and size, and an answer with tool calls is
  kept as JSON `{"text", "tool_calls"}`; a plain answer is still kept as plain text.
- A model or server that cannot do it: a rejection (HTTP 400 or 422) that is about tools,
  or any rejection of a forced `tool_choice`, becomes `no_tools`; one about images becomes
  `no_images`. A server that accepts a forced choice and then calls nothing is `no_tools`
  too. All three new kinds allow a fallback, so the role's next model is tried, and the
  message says to choose a model that can. Any other rejection is still final.
- What differs by provider type:

  | Type | Notes |
  |---|---|
  | `openai`, `openai_compatible` (OpenRouter and local servers included) | `tool_choice` is left out when it is `"auto"`, since some local servers do not know the field. A tool message takes text only, so an image inside a tool result is sent in a user message straight after the results. There is no error flag: a failed result's text is prefixed with `Error:`. A model's hidden reasoning is not carried between calls. |
  | `anthropic` | Images inside tool results are sent natively. Some models reject a forced `tool_choice`; that is the `no_tools` error. Thinking blocks are passed back as they came. |
  | `google` | Images inside tool results ride in the function response's `parts`, which older models may reject. A result's text is sent as `{"output": text}`, or `{"error": text}` when it failed. Thought signatures are passed back. |
  | `bedrock` | No `"none"`: the tools are left out instead, and once the conversation has tool calls in it `"none"` raises `no_tools`. Images inside tool results and the error status are taken by some model families only. Reasoning blocks are passed back as they came. |

## Embeddings

```python
reply = await brain(core).embed(
    ["first text", "second text"], actor="system", caller="memory.index", local_only=True)
reply.vectors, reply.provider_id, reply.model, reply.name, reply.input_tokens, reply.is_local
```

- One vector per text, in order, from the `embeddings` role's model. `embed_sync` is the
  same for worker jobs.
- Works for the `openai_compatible` and `openai` provider types: `POST {base}/embeddings`
  with `{"model", "input": [texts]}`, answered by `{"data": [{"index", "embedding"}],
  "usage": {"prompt_tokens"}}`. Any other type answers with the `no_embeddings` error and
  the role's next choice is tried.
- Texts go out in batches of 64. All vectors in one reply come from one model: if the
  first choice fails partway, the whole list is asked again of the next.
  `no_fallback=True` raises instead, for a caller that compares the vectors with ones it
  stored earlier.
- `local_only=True` means a hosted provider is never asked.
- One receipt per call: `model.called` with `embedding: true`, `inputs` (how many texts)
  and `output_tokens: 0`, or `model.failed` with `embedding: true` for each provider that
  was tried and failed. **The texts are never stored**, in the Vault or anywhere else;
  `keep_text` does not exist here.
- Usage counts embeddings apart from chat: each summary has an `embeddings` object
  (`calls`, `input_tokens`, `local_calls`, `local_input_tokens`, `estimated_cost`,
  `unpriced_calls`) and they are not in `calls`, `input_tokens` or `models`. Hosted
  embedding tokens are priced at the matching line's input price and included in the
  top-level `estimated_cost`.
- `brain(core).embeddings_target(local_only=...)` says which model would be asked, or why
  none can be, without contacting anyone.

## Layout

| File | Holds |
|---|---|
| `brain.py` | The `Brain`: configuration, routing, receipts, health, usage |
| `adapters/` | One adapter per provider type, over plain HTTP (boto3 for Bedrock) |
| `kinds.py` | Event kinds: `model.provider.saved`, `model.provider.removed`, `model.role.set`, `model.prices.set`, `model.called`, `model.failed` |
| `lenses.py` | `models.config` (providers, roles, prices) and `models.usage` (totals) |
| `prices.py`, `default_prices.toml` | Example prices and cost worked out at read time |
| `validate.py` | Checks on what the owner types, with readable messages |
| `state.py` | `models.db`: last health and cached model lists, shared by both processes |
| `routes.py` | The API under `/api/models` |

## Rules this module keeps

- **Keys** live only in the secret store, as `models.<provider id>.api_key`. The API says
  whether one is set. Error text from a provider is masked before anyone sees it.
- **No key, no contact.** A provider of a type that needs a key (`anthropic`, `openai`,
  `google`: `KEY_REQUIRED_TYPES` in `shapes.py`) and has none saved is never contacted:
  not for health, a model list, a test or a chat. It is reported as not set up, with a
  sentence telling the owner to add a key, and a role that names it passes over it to
  the next choice. Bedrock uses the machine's AWS credentials, and an OpenAI-compatible
  address may be the owner's own server that takes no key, so those are not held back.
- **Receipts.** Every answered call prints `model.called`; every failed attempt prints
  `model.failed` with the reason and our own sentence, never the prompt or the
  provider's words. Prompt and completion go to the Vault only when `keep_text` is true.
  A provider passed over without being asked (switched off, removed, no key, or hosted
  when the request must stay local) was not attempted and prints nothing. The provider
  Test lists models; where a provider has no model list it sends one fixed word through
  the same path, with the caller `models.test`, so it has a receipt and shows in usage.
- **Tokens, not money.** Events carry token counts. Cost is priced when a screen asks,
  from the owner's price table, so changing a price reprices history.
- **No model names in code.** Models are discovered from the provider or typed by the
  owner. The only names are patterns in the example price file.
- **Redirects are never followed**, so a key cannot be carried to another host.
- **Health checks list models.** They never send a prompt. There is no background
  job: a check runs when health is read (`GET /api/models/health`), at most once per
  provider per `models.health_interval_s`. A provider that answers but has no model
  list (a 404) counts as reachable.
- **A change forgets what was learned.** Changing a provider's type, address, region or
  profile, or saving or removing its key, drops its stored health and cached model
  list, so it reads as unchecked until the next check.
- **Only the owner can force a model list.** `?refresh=true` on
  `GET /api/models/providers/{id}/models` is honoured for the signed-in owner. A read
  token gets the cached list, renewed at most once per `models.models_cache_s`. A
  provider that was checked within `models.health_interval_s` is not asked again for a
  read token (`list_models(..., recheck=False)`): it gets the list already held, or the
  last error, so it cannot make a failing provider be called on every request.
- **An answer about the old place is dropped.** A check or call still on its way when
  the provider's address or key changes does not update the stored health or model list.
- **Local is worked out again when the address changes.** Changing a provider's address
  without saying whether it is local recomputes the flag from the new address, so an old
  "stays home" answer is never carried to a hosted address.
- **A stopped call still gets a receipt.** A stream the caller stops reading, or a call
  that is cancelled, prints `model.failed` with the reason `abandoned`.
- **The usage view is bounded.** Days, hours and the number of models per bucket are
  capped. Models past the cap are added together into one row with an empty provider and
  model, which is never priced.

## Models on a second machine

A model server on another machine of the owner's can be lent to this one as a node. It
appears here as an ordinary OpenAI-compatible provider, marked local, whose address is this
machine's own relay. Nothing in this module knows the difference. See [nodes.md](nodes.md).

## Adding a provider type

Write an `Adapter` subclass in `adapters/` with `list_models`, `chat` and `stream` (set
`streams_tools = True` once its `stream` returns tool calls on the final `RawReply`), add
it to `ADAPTERS` and to `PROVIDER_TYPES`, and add a fake for it in
`tests/test_models_fakes.py` so the shared adapter tests cover it. Check the vendor's
current API reference first; do not write the wire format from memory.

Usage days are UTC days: a lens fold may not read the owner's time zone setting.
