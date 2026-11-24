# WorkLog

The local record of work done with and without agents: what each task is, where it
stands, which chats worked on it and how much time it took.

## Language

**Task**:
A unit of work with a title and a project; it receives time from chats and manual entries.
_Avoid_: Item, element, card (a card is how the board shows a task)

**Chat**:
One conversation with an agent, bound to at most one task at a time; several chats may
work on the same task.
_Avoid_: Session, conversation, thread

**Session key**:
The identifier of a chat, written `agent:id` (for example `claude:ab12`).
_Avoid_: Session ID (that is only the `id` part)

**Active chat**:
A chat whose last turn ended less than the idle threshold ago.
_Avoid_: Open chat, running chat, current chat

**Chat title**:
The name the user gave a chat in its agent; chats without one are shown by their short ID.
_Avoid_: Chat name, label, summary

**Fork**:
A chat started from another chat's history, with its own session key.
_Avoid_: Branch, copy

## Task states

**Review**:
An open task whose chat work is finished but not yet delivered (verification, merge or release pending).
_Avoid_: Testing, to release, verifying

**Stalled task**:
A task in progress or in review with no recorded time for longer than the stall threshold.
_Avoid_: Stale, abandoned, inactive

## Task content

**Link**:
A path or URL that a task points to, such as its spec, plan or results file.
_Avoid_: Reference (that is the tracker key), attachment, artifact
