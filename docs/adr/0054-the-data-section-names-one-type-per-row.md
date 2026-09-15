---
status: accepted
extends: ADR-0025 (the app section groups by relationship, never by type — the same move, one scope
  over), ADR-0026 (the glossary holds names and words, and only names owe a key), ADR-0014 (the
  overlay renames prose, not identifiers), ADR-0035 (the panel is the Project's one list)
---

# The Data section names one type per row

Three labels stood over two things. The [[Resource Browser]] drew a `Data` group holding `Datasets`
and `Data Sources`; Browse Domino's sidebar offered `Data`, `Datasets` and `Data Sources` as three
peers, the first one's count being the sum of the next two. Nothing in `CONTEXT.md` said what `Data`
was, because it was not a decision — it arrived in #164 as somewhere for a group's add door to land,
and became vocabulary by sitting there.

The two words under it are Domino's own, and they are not the problem. The problem is that a person
reading them has to already know that one means *files held here* and the other means *a store
reached over a connection*, because the labels say neither.

## One section, and one type per row

There is one `Data` section. A row in it has a **type**, and the type says what the person is
looking at: **File volume** for a Dataset, **Data connection** for a Data Source.

The type is what a row is called on screen, in both surfaces. Browse Domino's sidebar filters by
these two words, the rail's subheads are these two words, and a catalogue row's meta line says the
same word again rather than the Domino noun. One word per row, said in one vocabulary.

## Reach and shape are one fact here, and that is a bet

A data thing has two properties that could each be a label: what shape it is (files, or rows and
columns) and how far it reaches (held by the Project, or connected out). `Data connection` names the
reach; `File volume` names the shape. They are not the same axis, and this ADR spends them as
though they were.

That is deliberate and it is worth writing down. Today every connected data row is a Data Source and
every Data Source is connected, so the two axes have exactly one cell each and a single word
identifies a row without ambiguity. A person reading `Data connection` learns the true thing.

The bet is on what Domino adds next. External Data Volumes and Domino Volumes for NetApp ONTAP are
file-shaped things reached over a connection. Sage models neither today. The day it models one, that
row is honestly both a File volume and a Data connection, and this scheme has no answer for it —
the section will need a third type, or it will need to split the two axes after all, with the type
naming shape and reach moving to a mark on the row.

An earlier draft of this ADR did split them, with `Tabular` as the second type and `connected` as a
word beside the noun. It was rejected for being a distinction with nothing on either side of it: two
labels and a rule for reading them, to describe one kind of thing, against a future Sage does not
have yet. When that future arrives this decision gets revisited, and the revisiting is cheap — the
words live in one map in `SW.util`, and both surfaces read it.

## The type replaces the Domino noun on these surfaces, and that costs the pack

A subhead per kind used to say `Datasets` and `Data Sources`, which a pack renames through
`{datasetPlural}` and `{dataSourcePlural}`. The type words are not names — there is no Domino thing
called a File volume — so under [ADR-0026](0026-the-glossary-holds-names-and-words-and-only-names-owe-a-key.md)
they are words, and a pack has nothing to rename them to.

So on these two surfaces a partner's customer now reads `File volume` and `Data connection` where
they used to read the partner's own nouns. That is a real reduction in what the overlay covers, and
it is accepted here on the grounds that the type says what the thing IS — a renamed Dataset still
holds files — and that the noun is still what every other surface says and what a pack still
renames everywhere else ([ADR-0014](0014-the-overlay-renames-prose-not-identifiers.md)).

An intermediate draft kept the noun on the row underneath the type. It drew `File volume` as a
subhead and `Dataset` on the row below it, which is two names for one thing, three lines deep, in a
320px rail — so the row line went and the subhead kept the word.

## `volume` on its own is not available

Domino sells a product called Volumes, and in this codebase *the project volume* is the Project's
own mounted directory. `Dataset`'s `_Avoid_` list said `volume` before this change and still does.
The type is the two words, and shortening it in a later edit reintroduces both collisions.

## Both subheads are drawn whenever their rows are

The panel named a subgroup only when a sibling subgroup also had rows. Under type labels that rule
produces a Project holding only Datasets whose rows read `Data`, beside a Project holding both whose
identical rows read `Data / File volume`. The type is a fact about the rows and not about what else
is in the section, so the Data group names its subheads unconditionally.

## The sidebar nests rather than flattens

Browse Domino keeps both types as filters — a person who wants only connections wants that filter —
but as children of `Data` rather than peers of it. Three peers whose first count is the sum of the
other two reads as a third kind of thing, which is how `Data` came to look like one.

## A row says each fact once

A catalogue row printed its Project twice: once as the description `in <project>`, and again as the
`originName` in its meta line. A Data Source has no Project, so the same slot fell back to the
platform's own name — true of every row in a catalogue of that platform. `ownerName` has been the
empty string since the modal was written and drew a separator with nothing after it.

All three are gone from the meta line, which now carries the type and what the platform actually
answered about the row. `api.js` still puts `originName` and `ownerName` on the row object: the
drawer reads `ownerName`, and `originName` now has no reader at all. Dropping it is a change to
`api.js` rather than to what the list draws, so it is left for whoever next opens that file.

## Two icons, drawn rather than typed

`📦` and `🔌` are gone: a File volume is `HddOutlined` and a Data connection is `ApiOutlined`, from
the icon set the Workbench already bundles, so this adds no dependency. `iconFor` still answers with
the emoji — a drawer title and a menu label both interpolate it into a string, and an element in a
template literal is `[object Object]` — and `iconNodeFor` is what the two lists render. Every other
kind still draws its emoji, so the two sets sit side by side until somebody decides the rest.

## Uploads stay their own section

A file a person drops into a Conversation is not Project data. No app can read one until it crosses
into a writable Dataset and becomes an Attachment
([ADR-0023](0023-an-upload-crosses-by-becoming-an-attachment.md)), and it is removed from the list
that owns it and no other. Folding `Files` into `Data` would group it with things that are already
readable and already bindable, which is the one thing an Upload is not.

Rejected: **`Tabular` as the second type, with `connected` as a word on the row.** Two labels and a
reading rule for one kind of thing; the axes it separates have one cell each until Sage models a
file-shaped connection. See the bet above, which is where this comes back if it does.
Rejected: **`Datasets` and `Data Sources` as the section's two subheads.** The state this replaces;
the labels name the things and say nothing about them, and `Data` above them is then an undefined
third word.
Rejected: **the type as a subhead and the Domino noun on the row below it.** Two names for one
thing, and a third line in a 320px rail.
Rejected: **`volume` as the one-word type.** Taken twice over, once by Domino and once by this
codebase.
Rejected: **folding `Files` into `Data`.** ADR-0023's crossing is the whole distinction.
