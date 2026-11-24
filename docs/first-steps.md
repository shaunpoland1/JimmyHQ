# First steps

You have installed JimmyHQ and made your owner account. This page is the short route from
an empty screen to something useful. Each app has its own guide, linked at the end.

## 1. Choose a model

Most of what JimmyHQ does needs a language model. Pick one before anything else:
[Models](models.md) says how to set up one on your own computer, on another computer you
own, or a hosted one with your own key. JimmyHQ uses your own model first and says on the
screen when it falls back to a hosted one.

If you want an app to search the web or read a page, also switch on **Settings, Web lookup**
(**Let models look things up on the web**) and choose a **Search provider**. It is off on a
new install, and an app that needs it says so in a sentence when it is off.

## 2. Add an app

Open **Apps**. A new install has none, so you will see **Install an app** and, further down,
a box called **Made by the JimmyHQ project**. That box lists the apps that ship with
JimmyHQ, such as **Posts**, **Reader**, **Articles**, **Books** and **Packages** (the
[README](../README.md#apps) has the full list). Each one is installed on its own,
with its own **Install** button. Installing one does not install the others. If you can see
only the app you just added, the rest are in that box, under your installed apps.

Before anything is installed you see what the app may do (for example which outside hosts it
may talk to) and you say yes. Then it appears in the sidebar.

Once installed:

- An app updates from its own page on the Apps screen (**Look for updates**, then
  **Update**). By default an update waits for your click. An update that asks for a new
  permission always waits for your yes, whatever you chose.
- You can switch an app off, or remove it, from the same screen. Removing keeps its data
  unless you say otherwise.
- A number beside an app in the sidebar is how many things wait for you in it, such as
  drafts to decide or parcels out for delivery. It is not an error count.

## 3. Where an app's settings and keys are

Open **Settings**, find the group **Apps**, and choose the app's name. That page holds:

- the app's settings, in plain words, with the less common ones hidden under advanced;
- a card **Keys and tokens** when the app uses an outside service. You paste a key into its
  field and press **Save**. The page then says only **Set** or **Not set**. A key is never
  shown again, not even to you; to change it, paste a new one, and **Clear** removes it;
- for Articles only, a card **Your own addresses**, where you give it your own website.

On the app's own page under **Apps**, the button **Open <app name> settings** takes you
there.

## 4. Agents and roles, in one paragraph

An app can work with agents or without them. Posts, Reader and Articles each name roles an
agent could hold (a researcher, a writer, a checker), and every role is optional. Leave them
empty and the app does that step itself, one step at a time, with your models. Fill one and
the agent does that step instead, using only the tools that role lists. You can choose when
you install (**Roles it would like filled**) or later under **Roles** on the app's page. A
writer and a checker cannot be the same agent. Packages has no roles.

## 5. Practice mode

Every app that sends something out starts in **Practice**, where it records what it would
have done and sends nothing:

- **Posts** books nothing until you choose another way under **Book through**.
- **Articles** publishes nothing until each site is set to **By hand** or **WordPress**.
- **Packages** looks nothing up until you choose **17TRACK** as the **Tracking service**.
- **Reader** has no Practice mode: it only searches the web and reads pages, and sends
  nothing of yours anywhere. It offers articles to other apps only when you press
  **Send on** or a topic is set to.

Try each app in Practice first. Switching from Practice is a setting you change yourself.

## 6. Back up

Under **Settings, Backups**, choose a folder, ideally on another drive. Backups are off until
you do. See [Back up and move your data](architecture.md#back-up-and-move-your-data).

## 7. Other AI apps

**Settings, Other AI apps** lets an outside assistant talk to JimmyHQ over MCP. It is off
until you switch it on, and today it is not offered the tools of Posts, Reader or Articles.
See [The MCP door](door.md).

## Guides for each app

- [Posts](posts.md): turn articles, topics and pictures into posts, and book them when you
  approve.
- [Reader](reader.md): find articles on topics you choose, and pass them on.
- [Articles](articles.md): research and write a full article, and publish it to your site as
  a draft.
- [Packages](packages.md): keep your parcels in one place.

Reader and Posts work together, and are most useful when both are installed. Each works alone.
