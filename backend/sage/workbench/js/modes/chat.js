window.SW = window.SW || {};

(function () {
  const { createElement: h, useEffect, useRef, Fragment } = React;
  const { Button, Tooltip } = antd;
  const { PlusOutlined, MenuFoldOutlined, MenuUnfoldOutlined } = icons;

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
  function TurnBar() {
    const { chatRunning, chatTurnThread, thread } = SW.store.get();
    if (!chatRunning) return null;
    const here = chatTurnThread && thread && chatTurnThread === thread.id;

    return h(
      'div',
      { className: 'sw-chat-turnbar' },
      h(
        'span',
        { className: 'sw-caption' },
        here
          ? SW.brand.text('{assistantName} is working on this conversation.')
          : SW.brand.text(
            '{assistantName} is working elsewhere in this project. One turn runs at a time.'
          )
      ),
      h(
        Button,
        { size: 'small', danger: true, onClick: () => SW.store.stopChat() },
        'Stop'
      )
    );
  }

  function Landing({ onSend, compact }) {
    const { starters, me, scope, turnWedged } = SW.store.get();
    const catalogue = (starters && starters.chat) || {};
    // Acme is a financial services firm; show its prompts alongside the
    // ones that make sense anywhere.
    const prompts = [
      ...(catalogue['cross-industry'] || []),
      ...(catalogue['financial-services'] || []),
    ].slice(0, compact ? 3 : 6);
    const placeholder = 'Ask about your data… use @ to bring in a resource';

    return h(
      'div',
      { className: `sw-landing${compact ? ' is-compact' : ''}` },
      h(
        'div',
        { className: 'sw-landing-inner' },
        h('h1', { className: 'sw-landing-title' }, `What do you want to know${me && me.name ? `, ${me.name.split(' ')[0]}` : ''}?`),
        h(
          'p',
          { className: 'sw-landing-sub' },
          scope.untitled
            ? 'Ask about your data. This project is saved; rename it when you want a lasting name.'
            : `Ask about data in ${scope.name}.`
        ),
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
            turnWedged } = SW.store.get();
    const scroller = useRef(null);

    useEffect(() => {
      if (threadId && (!thread || thread.id !== threadId)) {
        SW.store.openThread(threadId).catch(() => SW.router.replace('#/chat'));
      }
      if (!threadId && thread) SW.store.clearConversation();
    }, [threadId]);

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

    return h(
      'div',
      { className: 'sw-chat' },
      h(Rail, null),
      h(
        'div',
        { className: 'sw-chat-main' },
        empty
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
                      `Waiting for you to attach one ${SW.util.labelFor(pendingTurn.turn.waitsForAttachment)}.`,
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
              h(
                'div',
                { className: 'sw-composer-dock' },
                h(
                  'div',
                  { className: 'sw-composer-dock-inner' },
                  activePlanId &&
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
                  h(TurnBar, null),
                  h(SW.Composer, {
                    onSend: send,
                    placeholder: 'Ask about your data… use @ to bring in a resource',
                    disabled: turnWedged,
                  })
                )
              )
            )
      ),
      h(SW.PlanSheet, null)
    );
  };
})();
