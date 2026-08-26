window.SW = window.SW || {};

(function () {
  const { createElement: h, useEffect, useRef, Fragment } = React;
  const { Button, Tooltip } = antd;
  const { PlusOutlined, MenuFoldOutlined, MenuUnfoldOutlined } = icons;

  // The rail itself is shared with Build — same component, same behaviour. Chat
  // only adds the sandbox footnote, which is about the scope rather than about
  // the list.
  function Rail() {
    const { railHidden, scope } = SW.store.get();
    if (railHidden) return h(SW.ConversationRail, { mode: 'chat' });

    return h(
      'div',
      { className: 'sw-rail' },
      h(SW.ConversationRail, { mode: 'chat' }),
      scope.ephemeral &&
        h(
          'div',
          { className: 'sw-rail-foot' },
          h('span', { className: 'sw-scope-dot is-hollow' }),
          'Sandbox conversations are cleared when you leave.'
        )
    );
  }

  function Landing({ onSend, compact }) {
    const { starters, me, scope } = SW.store.get();
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
          h(SW.Composer, { onSend, autoFocus: true, placeholder })
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
    const { thread, messages, typing, pendingTurn, scope, activePlanId, planViewerId } = SW.store.get();
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
                      `Waiting for you to attach a ${SW.util.labelFor(pendingTurn.turn.waitsForAttachment)}.`,
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
                  h(SW.Composer, {
                    onSend: send,
                    placeholder: 'Ask about your data… use @ to bring in a resource',
                  })
                )
              )
            )
      ),
      h(SW.PlanSheet, null)
    );
  };
})();
