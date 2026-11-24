"""Settings for the models module. Importing this registers them."""

from __future__ import annotations

from jimmyhq.settings import Setting, declare, section

section("models", "Models and roles", group="models", order=10,
        description="The providers JimmyHQ may use, and which model does each kind of work.")

declare(Setting(
    key="models.connect_timeout_s", section="models", type="float", default=5.0, min=1, max=120,
    advanced=True,
    label="Give up connecting after (seconds)",
    help="How long to wait for a model provider to accept a connection before trying the next one.",
))
declare(Setting(
    key="models.read_timeout_s", section="models", type="float", default=300.0, min=5, max=3600,
    advanced=True,
    label="Give up waiting for an answer after (seconds)",
    help="How long a model may go quiet while writing before the call is abandoned. "
         "Slow local models need a generous value.",
))
declare(Setting(
    key="models.health_interval_s", section="models", type="int", default=30, min=10, max=3600,
    advanced=True,
    label="Check providers every (seconds)",
    help="How often JimmyHQ checks that each provider can be reached. The check lists "
         "models and never sends a prompt.",
))
declare(Setting(
    key="models.models_cache_s", section="models", type="int", default=300, min=0, max=86400,
    advanced=True,
    label="Remember a provider's model list for (seconds)",
    help="How long a discovered list of models is reused before asking the provider again.",
))
declare(Setting(
    key="models.default_max_tokens", section="models", type="int", default=4096, min=1,
    max=1_000_000,
    advanced=True,
    label="Longest answer (tokens)",
    help="The answer length limit used when neither the request nor the provider sets one.",
))
declare(Setting(
    key="models.max_image_bytes", section="models", type="int", default=3_500_000, min=1_000,
    max=50_000_000,
    advanced=True,
    label="Largest image sent to a model (bytes)",
    help="An image larger than this is refused before anything is sent. Hosted providers "
         "set their own limits, often around 5 MB once encoded, so raise this with care.",
))
declare(Setting(
    key="models.max_total_image_bytes", section="models", type="int", default=20_000_000,
    min=1_000, max=500_000_000,
    advanced=True,
    label="Most image data in one request (bytes)",
    help="All the images in one request, together, may not be larger than this. The request "
         "is refused before anything is sent. Hosted providers also limit a whole request, "
         "so raise this with care.",
))
declare(Setting(
    key="models.savings_reference", section="models", type="str", default="",
    portable=False,
    label="Compare local work against",
    help="The id of a line in your price table. Work done on your own machines is priced "
         "at that line to show what running locally saved. Leave empty to hide the figure.",
))
declare(Setting(
    key="models.currency", section="models", type="str", default="USD",
    portable=False,
    label="Currency of your price table",
    help="Shown next to estimated costs. Prices are per million tokens in this currency.",
))
