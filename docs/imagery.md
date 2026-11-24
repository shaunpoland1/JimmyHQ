# Imagery

Imagery makes pictures from a description with the image service you choose, keeps them in a
gallery you can search, and holds back at a daily limit you set.

## What to expect

Imagery starts on **Practice**, which draws a labelled placeholder picture on this computer
and sends nothing anywhere, so you can try everything first.

The **Gemini** and **Your own server** connections are built from the services' own
documentation and are not yet proven against the real services (see
[What was read, and what was not](#what-was-read-and-what-was-not)). Try one with a single
picture before you rely on it.

## Set it up

1. Install Imagery from **Apps** (see [First steps](first-steps.md)). The install window says
   it may talk to `generativelanguage.googleapis.com` (Gemini's address), that it expects
   answers of up to 20 MB and to wait up to 3 minutes for one, that it may ask you for the
   address of a server of your own, and that it may offer one read-only tool to your agents.
   What it expects is what Gemini is given, within the ceilings you set (step 3).
2. Open **Imagery**. On Practice there is nothing more to do: describe a picture, choose a
   **Style**, a **Shape** and **How many** (1 to 4, or fewer if you lowered **Most pictures in
   one go**), and press **Make**.
3. To use Gemini, nothing about your limits needs changing: Imagery says it expects pictures
   of several MB and a wait of up to 3 minutes from Gemini, and that is what Gemini is given,
   as long as your ceilings under **Settings, Apps, advanced** (**Largest network answer for
   an app (KB)** and **Longest wait for an app's network request (seconds)**) have not been
   lowered below it. Any other host is held to the smaller defaults.
   (The Apps page for Imagery also says, under "What it may do", when your ceilings are below
   what Imagery expects.) Then open **Settings, Apps, Imagery**:
   - Set **Image service** to **Gemini (Google)**.
   - Under **Keys and tokens**, paste your **Gemini key** (made in Google AI Studio) and press
     **Save**. It is never shown again.
   - **Gemini model** starts as `gemini-3.1-flash-image`. Google renames and retires image
     models, so change it when Gemini says the model is not found.
   - Image models often have no free quota: if you are told you are over quota at once, check
     that billing is on for the key's Google project.
4. To use a server of your own that answers the way OpenAI's image service does, set **Image
   service** to **Your own server (OpenAI style)**, then type **Your server's address**,
   **Your server's model**, and the **Sizes your server takes** (one per line, such as
   `3:2=1536x1024`). The rest depends on where the server is:
   - **A public https address** (`https://images.example.com/v1`). Under **Your own
     addresses**, add its host name (`images.example.com`) and type its key beside it, or
     leave the key empty if it needs none.
   - **A server on your own network or this computer** (`http://192.0.2.10:8000/v1`, a name
     on your tailnet, `http://localhost:8000/v1`). Type the address with its port as **Your
     server's address**. Then, on Imagery's page under **Settings, Apps**, find **Addresses on
     your own network** and add exactly the same scheme, name and port, without the path
     (`http://192.0.2.10:8000`); JimmyHQ asks you to confirm, since this lets Imagery send to
     and read from that address on your network and nothing else there. If the server needs a
     key, type it as **Key for a server on your own network**. Imagery sends it only to the address
     it was first sent to: if you change **Your server's address**, clear the key, press Make
     once, and type it again. Over `http://` a key crosses
     your network unscrambled, so use a key made for this server alone. Until you have added
     the address, Make fails at once with a sentence that says so and where to add it; nothing
     is sent, and it does not count against your daily limit.

## Make

- **What should the picture show?** is your description. **Style** adds one of your **Style
  endings** to the end of it (they are a setting, one per line as `Name: words`); the
  gallery keeps your description and the style's name apart.
- Making happens in the background and the page keeps asking how it is going, so a slow
  service does not time the page out. Each picture appears in the gallery as it is ready. If a
  request fails part-way, the pictures already made are kept and the sentence says how many.
- A failure is told in plain words: a bad key, a description the service's safety filter
  refused, over quota, no answer in time, or a busy service. Each says that nothing was made
  and what to do.
- **Start from a picture** (Gemini only) sends one picture of yours with the description, so
  the service changes it. Pick one on the Make page, or press **Use as starting picture** on one
  in the gallery. A picture you pick joins your gallery as **Your picture**.

## The daily limit

**Pictures a day with an outside service** (20 by default) is checked before anything is
sent: a request that would go over it is refused whole, with a sentence that says how many are
left. The day runs from midnight in your time zone. Every request counts, including one the
service refused or that took too long to answer, since it may still be charged. A request that
never left this computer (JimmyHQ refused it, or an address you have not added yet) does not.
The page shows today's count. Practice sends nothing and is never counted.

## The gallery

Newest first, each picture with its description, service, model and time, as a small picture.
You can **Search descriptions** (every word must be in it), show **Starred only**, **Star** a
picture, press **More like this** (it fills the Make page with that picture's description, style
and shape), **Use as starting picture**, **Save picture**, **Save details**, and **Delete**.

- **The large view.** Press a picture to open it. JimmyHQ draws it large (no larger than
  **Longest side of a picture shown in full (pixels)**, 2048 to begin with): it fits the
  screen, and a tap shows it bigger, to scroll. If JimmyHQ refuses to show it (larger than
  **Largest picture an app's screen may show in full (MB)**, or more than **Pictures an app's
  screen may show in full a minute**), the page says so in JimmyHQ's own words, shows the small
  picture instead and offers Try again. Raise those under Settings, Apps, advanced.
- **Save picture** (in the large view and on each card) hands you the picture as a file
  (`.png`, `.jpg` or `.webp`) through JimmyHQ's save, which needs your own click just before.
  It saves the picture as JimmyHQ drew it, so a picture larger than the full-view setting is
  saved at that size, and Imagery says so, with the setting to raise. A picture larger than
  **Largest file an app may offer you to keep (MB)** (8 to begin with) is refused, and Imagery
  names that setting. **Save details** still saves a Markdown note of the description and
  settings.
- **Save all starred** (shown when something is starred) saves your starred pictures one after
  another from one click, newest first, up to 200 at a time. JimmyHQ lets an app hand over only
  **Most files an app may offer from one click** (5 to begin with) and **Most files an app may
  offer in one minute** (12), and each must follow the one before within a few seconds. Imagery
  cannot see those numbers; it saves until JimmyHQ refuses one, stops there, and says how many it
  saved, why it stopped (in JimmyHQ's words) and how many are left. The button then reads **Save
  the other N starred**: press it again, and a minute later if the minute limit stopped it, to go
  on. Imagery never presses for you. A picture too large to show or save is left out, and the
  rest go on.

- **Delete** asks once, inside the page, and removes the picture's file (overwritten, then
  removed) and its row (overwritten in the database). Nothing is kept of it.
- **Most pictures to keep** (300) and **Most space for pictures (MB)** (400) are the limits.
  When a new picture takes the gallery over one, the oldest pictures you have **not starred**
  are deleted for good, never the one just made, and the Gallery page says how many. A gallery
  full of starred pictures refuses new ones and says so, since nothing may be deleted to make
  room. Keep **Most space** below the app's storage line (500 MB to begin with, under
  Settings, Apps, Limits for one app).

## What it will never do without you

- Send anything on Practice.
- Send more than the daily limit, or send a request you did not press Make for.
- Put a description, a picture or a key on your record. Imagery writes nothing there. Your
  keys are kept by JimmyHQ's secret store and are taken out of any sentence, log or file.
- Delete a starred picture to make room.

## What your agents may do

Imagery offers agents one tool, **recent**, which lists the pictures made lately (number,
time, service, model, shape, star, and your own description, shown as written). It only reads.
It gives no picture, key or address, and none of the pictures you gave it. You choose which
agents get it under **Village**. There is no tool that makes a picture: a tool that spends money
would have to wait on the ladder for each agent, and no first-party app offers one yet.

## What was read, and what was not

**Gemini** (read on 23 November 2026, on Google's own pages):

- The image guide's `generateContent` variant (`aistudio.google.com/docs/image-generation.md?generate-content=true`):
  the REST call `POST models/<model>:generateContent` with the key in the `x-goog-api-key`
  header; a text part, and for editing a second part `inline_data` with `mime_type` and
  base64 `data`; `generationConfig.responseModalities` `["TEXT", "IMAGE"]` and
  `imageConfig.aspectRatio` and `imageSize` (`1K`, `2K`, `4K`, capital K; `512` on one model);
  up to 14 reference images; every image has a SynthID watermark.
- The `generateContent` reference (`aistudio.google.com/api-reference/generate-content`): the
  response shape, with `inlineData` (`mimeType`, `data`) in `candidates[].content.parts[]`;
  the finish reasons `IMAGE_SAFETY`, `IMAGE_PROHIBITED_CONTENT`, `IMAGE_OTHER`, `NO_IMAGE`,
  `IMAGE_RECITATION`, `SAFETY`, `PROHIBITED_CONTENT`, `BLOCKLIST`; `promptFeedback.blockReason`
  (when set, no candidates come back); the 14 aspect ratios Imagery offers with Gemini.
- The errors page (`.../docs/api-errors.md?generate-content=true`): statuses 400
  (`INVALID_ARGUMENT`, `FAILED_PRECONDITION`), 402, 403, 404, 429, 499, 500, 503, 504 and the
  body `{"error": {"code", "message", "status", "details"}}`.
- The models page (`aistudio.google.com/docs/models`): the image model names
  `gemini-nano-banana-2.1`, `gemini-3.1-flash-image`, `gemini-3.1-flash-lite-image`,
  `gemini-3-pro-image` and `gemini-2.5-flash-image`.

**Could not be confirmed, so these are guesses:**

- That `generateContent` stays available: Google's index calls it the "legacy" API and names
  the newer **Interactions API** (`POST /v1beta/interactions`) the recommended default for new
  work. Imagery uses `generateContent` because its request and answer are fully documented
  there; the Interactions guide shows requests but no raw answer. Moving to it would change
  only `imagery/providers/gemini.py`.
- That every model name above works with `generateContent` (the default
  `gemini-3.1-flash-image` is the one an earlier version of JimmyHQ used successfully with it;
  the models page does not say which methods each supports).
- `v1beta` against `v1`: the guide's samples use `/v1/`, the reference `/v1beta/`. Imagery uses
  `v1beta`.
- The most bytes a starting picture may be (Imagery caps it at 7 MB by default), and which
  picture types Gemini takes (Imagery sends PNG, JPEG and WebP).
- Whether `["TEXT", "IMAGE"]` is needed or `["IMAGE"]` alone would do.
- That a bad key is answered `400 INVALID_ARGUMENT` with `API_KEY_INVALID` (Imagery also
  accepts 401 and 403 as a key problem).

**Your own server**: the request is OpenAI's image API as its reference describes it
(`developers.openai.com/api/reference/resources/images`): `model`, `prompt`, `n`, `size`, and
`response_format` (`b64_json`), answered with `data[].b64_json`. Its newer models do not take
`response_format`, which is why **Ask your server for the picture itself** is a setting. That
page does not describe an error body; Imagery reads `{"error": {"message", "code", "type"}}`,
a plain `error` text, or `detail`, and treats a code with "policy", "safety", "moderation" or
"content_filter" in it as a refusal of your words. These are guesses.

**A starting picture for your own server is left out.** OpenAI's pages (read on 23 November
2026) describe `/images/edits` in more than one shape: the image guide's curl examples send
multipart form data with `image[]` (several) or `image` (one) beside `prompt` and `model`, while
the reference page documents a JSON body whose `images` array holds `image_url` or `file_id`,
and says nothing of other servers taking either. Servers that copy the generations endpoint
differ on which they copy, so there is no one documented shape to build on without guessing.
**Start from a picture** stays with Gemini.

## What JimmyHQ's app platform limits

- **Large answers and long waits.** JimmyHQ refuses a network answer over what the app's
  manifest expects of that host, and stops waiting after what it expects, never past your
  ceilings **Largest network answer for an app (KB)** and **Longest wait for an app's network
  request (seconds)**. Imagery's manifest says it expects answers up to 20 MB and a wait up to
  3 minutes from Gemini, which tells you at install and on its Apps page; a host it says
  nothing about gets only the small defaults (1 MB, 20 seconds). When your ceiling is the
  lower one, Imagery says which setting ended the
  request and what to raise it to, and that the request may still have been charged (it counts
  against your daily limit). **Wait for the service for (seconds)** in Imagery's advanced
  settings (120 to begin with) is how long Imagery itself asks one request to take, never
  more than your limit.
- **Your own server.** JimmyHQ reaches a public https host you add under Your own addresses,
  or exactly one address on your own network that you add under **Addresses on your own
  network** (up to **Addresses on your own network one app may be given**, 3 to begin with).
  Only that scheme, name and port; never another port or name for the same computer, a cloud
  computer's metadata address, or JimmyHQ's own port. The name is checked again at every request.
  Imagery cannot ask whether you have added it: it sends, and a refusal becomes a sentence that
  says where to add it.
- **Pictures on the page.** The gallery shows small pictures (**Longest side of a small
  picture**, 320 pixels to begin with) and the large view the full view above.
- **Saving a picture.** See the gallery. A saved file may be at most **Largest file an app may
  offer you to keep (MB)** and only a few come from one click.
- **The Tape.** Imagery writes nothing on your record.

## What it leaves out for now

- Making a picture from an agent's tool, and offering pictures to other apps (Posts could use
  them).
- Other services (Imagen is shut down; others can be added in `imagery/providers/`).
- Saving the picture file exactly as it was made when it is larger than JimmyHQ draws in full:
  what is saved is the picture as JimmyHQ drew it.
- A starting picture for your own server (see above).
- Several pictures from one request to Gemini at once (each picture is its own request, so a
  failure part-way keeps what was made).
