# Part 1: MCP and the Fetch MCP Server

## The Model Context Protocol

The Hugging Face article starts from the integration problem: M AI applications and
N tools used to need M×N custom connectors. MCP, which Anthropic open-sourced in late 2024, replaces those with
one open protocol. Each side implements it once, so M×N becomes M+N. The article uses the USB-C
analogy: one standard plug instead of a drawer full of adapters.

The architecture has three roles:

- **Host**: the AI application the user works in (a desktop assistant, an IDE,
  an agent). It owns the model and the conversation.
- **Client**: lives inside the host and keeps a one-to-one connection with one
  server, handling the handshake, capability listing, calls and results.
- **Server**: a lightweight program that exposes capabilities, mainly **tools**
  (actions the model chooses to invoke), **resources** (data the application reads
  into context) and **prompts** (reusable templates the user picks).

Because the server describes its own capabilities at runtime, a host can discover
and use a new server without code changes. This is what separates MCP from plain
function calling, where every tool is hard-wired into one application. The article
notes that authentication, security and remote deployment were still maturing.

## The Fetch MCP Server, critiqued

The reference Fetch server (`mcp-server-fetch`) is largely single-purpose. It
exposes one tool, `fetch`, which retrieves a URL and
converts the HTML to Markdown, plus a `fetch` prompt for fetches the user starts.

**Well-defined interface.** Every argument is typed and described, with explicit
bounds (`max_length` 1–999999, `start_index` ≥ 0). Arguments are strict, so `"10"`
is not quietly converted to `10`. Annotations mark it read-only and idempotent.

**Predictable behaviour.** Long pages are paginated, and a truncated result says
exactly which `start_index` to request next. Expected failures (robots.txt refusal,
an unreachable host, a private-network address) are raised as `ToolError` and reach
the model as error results instead of being swallowed.

**Where it slips.** The `raw` flag puts a second output mode (raw HTML instead of
Markdown) inside the same tool. The tool description also tries to persuade the
model ("this tool now grants you internet access") instead of describing the
interface. And some outcomes ("No more content available", the truncation notice)
come back as `<error>` text inside a *successful* result, so a client must parse strings to tell
them apart from page content. It is isolated and well
documented, but inconsistent about what counts as an error.
