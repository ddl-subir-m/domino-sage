window.SW = window.SW || {};

(function () {
  const { createElement: h, useEffect, useRef, Fragment } = React;
  const { Button, Skeleton } = antd;

  // The rail itself is shared with Build — same component, same behaviour. Chat
  // only adds the layout wrapper the docked rail needs.
  function Rail() {
    const { railHidden } = SW.store.get();
    if (railHidden) return h(SW.ConversationRail, { mode: 'chat' });

    return h(
      'div',
      { className: 'sw-rail' },
      h(SW.ConversationRail, { mode: 'chat' })
    );
  }

  // What a running turn looks like from the composer. Chat had no answer to "it is still going"
  // beyond waiting: the server refused the next question in the transcript, which read as Sage
  // replying to a question about data with a complaint about a build, and there was nothing on
  // screen to press. One project runs one turn, so this is also what a turn started in another
  // conversation looks like from here — say which, because "wait" and "wait, over there" send
  // someone to different places.
  //
  // It no longer means "you cannot ask anything else": a second question queues (#79), and the
  // composer below stays open. What it still means is that there is one turn to Stop.
  //
  // `chatRunning` says the Project is busy; it never said WHICH turn, so this bar used to offer a
  // Stop over a Build turn and revert somebody's app (#126). The turn has a name now, so the Stop
  // shows only for the Chat turn in this conversation and everything else gets the line.
  function TurnBar() {
    const { chatRunning, thread } = SW.store.get();
    if (!chatRunning) return null;
    const here = SW.store.runningTurnHere('chat', thread && thread.id);
    const away = here ? null : SW.store.runningTurnElsewhere('chat', thread && thread.id);

    return h(
      'div',
      { className: 'sw-chat-turnbar' },
      h(
        'span',
        { className: 'sw-caption' },
        here
          ? SW.brand.text('{assistantName} is working on this conversation.')
          : (away.href ? h('a', { href: away.href }, away.text) : away.text)
      ),
      here && h(
        Button,
        { size: 'small', danger: true, onClick: () => SW.store.stopChat() },
        'Stop'
      )
    );
  }

  // What an open investigation looks like from the composer (#386, ADR-0056).
  //
  // The transcript records when the grant was made; this says it is still standing. That is the
  // half the review of #381's design asked for and the transcript alone cannot give: a line that
  // scrolls away says a capability was granted, and somebody twenty turns later is looking at a
  // conversation whose turns keep a shell for a reason nothing on screen mentions.
  //
  // CLOSING IS NOT DELETING and the sentence says so, because the two were one act until this
  // ticket. What was measured stays; what stops is the reaching.
  //
  // Drawn off the Thread's own record rather than off a live frame, unlike every card in the
  // transcript. A card's buttons belong to the person who watched it arrive; this is a fact about
  // the conversation, true on a reload, in a second tab, and a week later.
  function InvestigationBar() {
    const { thread } = SW.store.get();
    // The same helper the card's two buttons use, and here for the one thing it adds over a local
    // flag: a failed close SAYS so. Hand-rolled, the spinner stopped, the bar went on reading
    // "Investigating", and the person believed the capability had been taken back when the POST had
    // 404'd — the quiet direction, on the control that exists to take it back.
    const [busy, run] = SW.util.useBusyAct();
    const open = thread && ((thread.context || {}).investigation || {}).state === 'open';
    if (!open) return null;

    return h(
      'div',
      { className: 'sw-chat-planbar' },
      h('span', { className: 'sw-caption' },
        SW.brand.text('Investigating — {turnPlural} here can query your {dataSourcePlural}')),
      h(
        Button,
        {
          size: 'small',
          loading: !!busy,
          disabled: !!busy,
          onClick: run('close', () => SW.store.closeInvestigation(thread.id)),
        },
        'Close investigation'
      )
    );
  }

  function Landing({ onSend, compact }) {
    const { starters, me, turnWedged } = SW.store.get();
    const catalogue = (starters && starters.chat) || {};
    // Acme is a financial services firm; show its prompts alongside the
    // ones that make sense anywhere.
    const prompts = [
      ...(catalogue['cross-industry'] || []),
      ...(catalogue['financial-services'] || []),
    ].slice(0, compact ? 3 : 6);
    const placeholder = SW.util.composerPlaceholder('Ask about your data');

    return h(
      'div',
      { className: `sw-landing${compact ? ' is-compact' : ''}` },
      h(
        'div',
        { className: 'sw-landing-inner' },
        h('h1', { className: 'sw-landing-title' }, `What do you want to know${me && me.name ? `, ${me.name.split(' ')[0]}` : ''}?`),
        h(
          'div',
          { className: 'sw-landing-composer' },
          h(TurnBar, null),
          h(SW.Composer, { onSend, autoFocus: true, placeholder, disabled: turnWedged })
        ),
        h(
          'div',
          { className: 'sw-starters' },
          prompts.map((prompt) =>
            h(
              'button',
              {
                key: prompt.title,
                className: 'sw-starter',
                disabled: turnWedged,
                onClick: () => onSend(prompt.prompt),
              },
              h('span', { className: 'sw-starter-text' }, prompt.title),
              h('span', { className: 'sw-starter-detail' }, prompt.detail)
            )
          )
        )
      )
    );
  }

  SW.ChatMode = function ChatMode({ threadId }) {
    const { thread, messages, typing, pendingTurn, scope, activePlanId, planViewerId,
            turnWedged, openingThreadId } = SW.store.get();
    const scroller = useRef(null);

    // Read during render rather than subscribed to: `Root` already re-renders on every route
    // emission, so this mode is re-rendered with the fresh count without a second subscription to
    // the same router.
    const { nav } = SW.router.get();

    // `nav` and not `threadId` alone. Clicking the row already in the hash re-emits the SAME
    // threadId (`SW.router.go` calls `handleChange` rather than returning early), so on
    // `[threadId]` this effect did not re-run and a stranded view could not be recovered from the
    // one control that looks like it should do it — the row itself (#455).
    //
    // Safe to re-run on a navigation that changed nothing, because the guard below is about where
    // the STORE is, not where the route is: a conversation already open re-runs to a no-op.
    //
    // `openingThreadId` is the third arm, and it is what makes the second click on a row that IS
    // loading free rather than a restart — the complaint this ticket came from is people clicking
    // again. It cannot swallow a recovery: the marker names whichever open is current, so a lost
    // generation has already had it taken off it by the open that won.
    useEffect(() => {
      if (threadId && (!thread || thread.id !== threadId) && openingThreadId !== threadId) {
        SW.store.openThread(threadId).catch(() => SW.router.replace('#/chat'));
      }
      if (!threadId && thread) SW.store.clearConversation();
    }, [threadId, nav]);

    useEffect(() => {
      const el = scroller.current;
      if (el) el.scrollTop = el.scrollHeight;
    }, [messages.length, typing]);

    // A streaming answer grows the last message rather than adding one, so the length of the list
    // does not change and the view stops following the text. Follow it — but only from the bottom.
    // Being yanked back down every frame while reading something further up is worse than not
    // following at all.
    const streamedChars = messages.length
      ? (messages[messages.length - 1].blocks || []).reduce((n, b) => n + (b.value || '').length, 0)
      : 0;

    useEffect(() => {
      const el = scroller.current;
      if (!el) return;
      if (el.scrollHeight - el.scrollTop - el.clientHeight < 120) el.scrollTop = el.scrollHeight;
    }, [streamedChars]);

    const send = async (text) => {
      if (!thread) {
        const created = await SW.store.newThread();
        SW.router.replace(`#/chat/${created.id}`);
      }
      await SW.store.sendMessage(text);
    };

    const openGraduation = () => SW.store.set({ graduationOpen: true });

    const empty = !thread || messages.length === 0;

    // A Conversation other than the one on screen is arriving. Drawn over the turns rather than
    // beside them, because what is standing there belongs to the Conversation being left and
    // reading it as the answer to the row you just clicked is the whole complaint (#455).
    //
    // Compared against `thread` rather than read alone: `openThread` also runs for the
    // Conversation already open (a refresh after a handoff, say), and a skeleton there would
    // blink the turns off and back over work nobody navigated away from.
    const opening = !!openingThreadId && (!thread || thread.id !== openingThreadId);

    // Hoisted out of the branch below so it can stand beside the skeleton as well as beside the
    // turns. It has to survive the open: `chatRunning` is project-wide, so a turn can be running
    // while you open a different Conversation, and `TurnBar` is where Stop lives — the one way
    // out of a wedged turn (ADR-0043). Unmounting the dock for the interval took Stop off the
    // screen and took whatever was half-typed in the Composer with it, since the draft is the
    // Composer's own state.
    const dock = h(
      'div',
      { className: 'sw-composer-dock' },
      h(
        'div',
        { className: 'sw-composer-dock-inner' },
        // Not while another Conversation is arriving. The plan, and the app the second button
        // names, belong to the Conversation being LEFT — drawing them over a skeleton is the
        // fault this ticket is about, one bar further down the screen.
        !opening && activePlanId &&
          h(
            'div',
            { className: 'sw-chat-planbar' },
            h('span', { className: 'sw-caption' }, 'Working from a plan'),
            h(
              Button,
              {
                type: 'link',
                size: 'small',
                style: { padding: 0 },
                onClick: () => SW.store.openPlanArtifact(activePlanId),
              },
              'Open plan'
            ),
            // Once this conversation has changed an app, Build is
            // somewhere to go back to rather than a thing to start.
            h(
              Button,
              {
                size: 'small',
                onClick: () => SW.store.draftHandoffPlan(thread && thread.id),
              },
              thread && (thread.touched || []).length ? 'Open in Build' : 'Build this'
            )
          ),
        h(InvestigationBar, null),
        h(TurnBar, null),
        h(SW.Composer, {
          onSend: send,
          placeholder: SW.util.composerPlaceholder('Ask about your data'),
          // Typing survives the open; SENDING must not. `send` posts through `state.thread`,
          // which is still the Conversation being left, so a message sent here would land in the
          // one nobody is looking at — the same mis-delivery the `openSeq` guard exists to stop,
          // reached through the composer instead of through a race.
          disabled: turnWedged || opening,
        })
      )
    );

    return h(
      'div',
      { className: 'sw-chat' },
      h(Rail, null),
      h(
        'div',
        { className: 'sw-chat-main' },
        opening
          ? h(
              Fragment,
              null,
              h(
                'div',
                { className: 'sw-messages sw-scroll' },
                h(
                  'div',
                  { className: 'sw-messages-inner sw-messages-opening' },
                  // No title row: a Conversation's turns are what is being waited for, and a grey
                  // bar where the heading goes would promise one this pane does not draw.
                  h(Skeleton, { active: true, title: false, paragraph: { rows: 3 } }),
                  h(Skeleton, { active: true, title: false, paragraph: { rows: 5 } })
                )
              ),
              dock
            )
          : empty
          ? h(Landing, { onSend: send, compact: !!planViewerId })
          : h(
              Fragment,
              null,
              h(
                'div',
                { className: 'sw-messages sw-scroll', ref: scroller },
                h(
                  'div',
                  { className: 'sw-messages-inner' },
                  messages.map((message) =>
                    h(SW.Message, { key: message.id, message, onSave: openGraduation })
                  ),
                  typing && h(SW.TypingIndicator, { label: typing }),
                  pendingTurn &&
                    h(
                      'div',
                      { className: 'sw-waiting' },
                      // The kind's label is a pack noun now, and there is no article engine, so
                      // "one Dataset" rather than "a Dataset" — a pack renaming the noun to
                      // something vowel-initial would have made the article wrong.
                      //
                      // The type word where the kind has one, because the button below opens the
                      // rail on this same kind and the rail says `Pick a File volume to continue`
                      // (ADR-0054). Two words for one thing, three lines apart, is the reading that
                      // ADR set out to remove.
                      `Waiting for you to attach one ${SW.util.dataTypeLabel(pendingTurn.turn.waitsForAttachment)
                        || SW.util.labelFor(pendingTurn.turn.waitsForAttachment)}.`,
                      h(
                        Button,
                        {
                          type: 'link',
                          size: 'small',
                          onClick: () => SW.store.focusPanel(pendingTurn.turn.waitsForAttachment),
                        },
                        'Open the panel'
                      )
                    )
                )
              ),
              dock
            )
      ),
      h(SW.PlanSheet, null)
    );
  };
})();
