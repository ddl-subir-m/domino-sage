window.SW = window.SW || {};

(function () {
  const { createElement: h, useState, useEffect } = React;
  const { Button, Table, Tooltip, Tag, Space } = antd;
  const {
    CopyOutlined, RightOutlined, DownOutlined, PushpinOutlined, ReloadOutlined,
    ExportOutlined, DownloadOutlined, ThunderboltOutlined,
  } = icons;

  function CodeBlock({ code, language }) {
    const [expanded, setExpanded] = useState(false);
    const lines = String(code).split('\n');
    const long = lines.length > 20;
    const shown = long && !expanded ? lines.slice(0, 20).join('\n') : code;

    return h(
      'div',
      { className: 'sw-code-wrap' },
      h('pre', { className: 'sw-code' }, SW.util.highlight(shown, language)),
      h(
        Tooltip,
        { title: 'Copy code' },
        h(Button, {
          size: 'small',
          className: 'sw-code-copy',
          icon: h(CopyOutlined, null),
          'aria-label': 'Copy code',
          onClick: () => SW.util.copy(code, 'Code copied'),
        })
      ),
      long &&
        h(
          Button,
          { type: 'link', size: 'small', onClick: () => setExpanded(!expanded) },
          expanded ? 'Show less' : `Show all ${lines.length} lines`
        )
    );
  }

  function SandboxRun({ block }) {
    const [open, setOpen] = useState(false);
    return h(
      'div',
      { className: 'sw-sandbox-run' },
      h(
        'button',
        { className: 'sw-sandbox-toggle', onClick: () => setOpen(!open) },
        h(open ? DownOutlined : RightOutlined, { style: { fontSize: 9 } }),
        h('span', null, `${block.label || 'Ran Python'} · ${(block.durationMs / 1000).toFixed(1)}s`),
        block.packages && h('span', { style: { color: '#8F8FA3' } }, `· ${block.packages}`)
      ),
      open &&
        h(
          'div',
          { className: 'sw-sandbox-detail' },
          h(CodeBlock, { code: block.code, language: 'python' }),
          block.stdout &&
            h(
              'div',
              null,
              h('div', { className: 'sw-stdout-label' }, 'stdout'),
              h('pre', { className: 'sw-code' }, block.stdout)
            )
        )
    );
  }

  function ChartBlock({ chartId, onAddToPlan }) {
    const { charts } = SW.store.get();
    const spec = charts[chartId];
    if (!spec) return null;
    return h(
      'div',
      { className: 'sw-block-card' },
      h(
        'div',
        { className: 'sw-block-head' },
        h(
          'div',
          { style: { flex: 1, minWidth: 0 } },
          h('div', { className: 'sw-block-title' }, spec.title),
          spec.subtitle && h('div', { className: 'sw-block-sub' }, spec.subtitle)
        ),
        h(
          Space,
          { size: 2 },
          h(
            Tooltip,
            { title: 'Open full size' },
            h(Button, {
              type: 'text',
              size: 'small',
              icon: h(ExportOutlined, null),
              'aria-label': 'Open chart',
              onClick: () => antd.message.info('Full-size charts are not wired up in this prototype.'),
            })
          ),
          h(
            Tooltip,
            { title: 'Export as PNG' },
            h(Button, {
              type: 'text',
              size: 'small',
              icon: h(DownloadOutlined, null),
              'aria-label': 'Export chart',
              onClick: () => antd.message.success('Chart exported to examples/'),
            })
          ),
          onAddToPlan &&
            h(
              Tooltip,
              { title: 'Add to plan' },
              h(Button, {
                type: 'text',
                size: 'small',
                icon: h(PushpinOutlined, null),
                'aria-label': 'Add chart to plan',
                onClick: onAddToPlan,
              })
            )
        )
      ),
      h('div', { className: 'sw-block-body' }, h(SW.Chart, { options: spec.options, height: 260 }))
    );
  }

  function TableBlock({ block }) {
    const [showAll, setShowAll] = useState(false);
    const columns = block.columns.map((name, index) => ({
      title: name,
      dataIndex: index,
      key: name,
      ellipsis: true,
      align: index === 0 ? 'left' : 'right',
      render: (value) => {
        if (value === 'Breach') return h(Tag, { color: 'error', bordered: false }, 'Breach');
        if (value === 'Watch') return h(Tag, { color: 'warning', bordered: false }, 'Watch');
        if (value === 'OK') return h(Tag, { bordered: false }, 'OK');
        if (value === 'Awaiting review') return h(Tag, { color: 'blue', bordered: false }, value);
        if (value === 'Auto-cleared') return h(Tag, { color: 'success', bordered: false }, value);
        return value;
      },
    }));
    const all = block.rows.map((row, index) => ({ key: index, ...row }));
    const rows = showAll ? all : all.slice(0, 10);

    return h(
      'div',
      { className: 'sw-block-card' },
      block.title &&
        h('div', { className: 'sw-block-head' }, h('div', { className: 'sw-block-title' }, block.title)),
      h(Table, { size: 'small', pagination: false, dataSource: rows, columns, scroll: { x: true } }),
      all.length > 10 &&
        h(
          'div',
          { style: { padding: '8px 16px' } },
          h(
            Button,
            { type: 'link', size: 'small', style: { padding: 0 }, onClick: () => setShowAll(!showAll) },
            showAll ? 'Show fewer' : `Show all ${all.length} rows`
          )
        )
    );
  }

  function PlanSuggestion() {
    return h(
      'div',
      { className: 'sw-suggestion' },
      h(
        'div',
        { className: 'sw-suggestion-title' },
        h(ThunderboltOutlined, { style: { color: '#543FDE' } }),
        'This is starting to look like an app.'
      ),
      h(
        'div',
        { className: 'sw-suggestion-detail' },
        'I can write a plan so you can review it, share it, and build from it.'
      ),
      h(
        Space,
        { size: 8 },
        h(
          Button,
          { type: 'primary', size: 'small', onClick: () => SW.store.draftPlan() },
          'Write a plan'
        ),
        h(
          Button,
          { size: 'small', onClick: () => SW.store.dismissPlanSuggestion() },
          'Not now'
        )
      )
    );
  }

  function GraduationNudge({ onSave }) {
    const { thread, resourceGroups } = SW.store.get();
    const files = (resourceGroups.file || []).filter((f) => f.sandbox);
    const artifacts = (thread && thread.artifacts) || [];
    const parts = [];
    if (files.length) parts.push(files.map((f) => f.name).join(', '));
    if (artifacts.length) parts.push(`${artifacts.length} ${artifacts.length === 1 ? 'chart' : 'charts'}`);

    return h(
      'div',
      { className: 'sw-nudge' },
      h('span', { className: 'sw-scope-dot is-hollow', style: { marginTop: 5 } }),
      h(
        'div',
        { className: 'sw-nudge-main' },
        h('div', null, "You're in ", h('strong', null, 'Personal sandbox'), '. ',
          parts.length
            ? `${parts.join(' and ')} will be cleared when you leave.`
            : 'Anything you create here is cleared when you leave.'),
        h(
          'div',
          { style: { marginTop: 8 } },
          h(Space, { size: 8 },
            h(Button, { type: 'primary', size: 'small', onClick: onSave }, 'Save to a project'),
            h(Button, { type: 'text', size: 'small', onClick: () => SW.store.dismissNudge() }, 'Dismiss'))
        )
      )
    );
  }

  // A change that happened, attached to the app it happened to. This is where
  // Review and Publish belong: a turn can change two apps, and the preview can
  // only show one of them, so the entry — not the panel — is what makes every
  // change reviewable. Nothing a turn did is left for the user to go and find.
  function AppChange({ block }) {
    const { activeApp, thread } = SW.store.get();
    const [app, setApp] = useState(null);

    useEffect(() => {
      let cancelled = false;
      SW.api
        .app(block.appId)
        .then((loaded) => {
          if (!cancelled) setApp(loaded);
        })
        .catch(() => {});
      return () => {
        cancelled = true;
      };
    }, [block.appId]);

    if (!app) return null;
    // Chat has no preview panel, so the app can only be "in the preview" while
    // Build is the mode on screen — otherwise the card offers to take you there.
    const showing =
      SW.router.get().mode === 'build' && activeApp && activeApp.id === app.id;

    return h(
      'div',
      { className: `sw-appchange${showing ? ' is-showing' : ''}` },
      h(
        'div',
        { className: 'sw-appchange-head' },
        h(SW.StatusDot, { status: app.status }),
        h('span', { className: 'sw-appchange-name' }, app.name),
        showing && h('span', { className: 'sw-appchange-here' }, 'in the preview')
      ),
      block.summary && h('div', { className: 'sw-appchange-summary' }, block.summary),
      h(
        'div',
        { className: 'sw-appchange-foot' },
        !showing &&
          h(
            Button,
            {
              size: 'small',
              onClick: () =>
                SW.router.go(`#/build${thread ? `/${thread.id}` : ''}?app=${app.id}`),
            },
            'Open in preview'
          ),
        h(
          'span',
          { className: 'sw-caption' },
          app.visibility === 'private'
            ? 'Not published yet'
            : `Published · ${SW.util.relativeTime(app.lastDeploy)}`
        )
      )
    );
  }

  function FileCard({ block }) {
    const href = `./api/project/file/raw?path=${encodeURIComponent(block.path || '')}`;
    return h(
      'a',
      { className: 'sw-block-card', href, style: { display: 'block', padding: '10px 14px' } },
      block.name || block.path
    );
  }

  function ImageBlock({ block }) {
    return h(
      'div',
      { className: 'sw-block-card' },
      block.title &&
        h('div', { className: 'sw-block-head' }, h('div', { className: 'sw-block-title' }, block.title)),
      h(
        'div',
        { className: 'sw-block-body' },
        h('img', { src: block.src, alt: block.title || '', style: { maxWidth: '100%', display: 'block' } })
      )
    );
  }

  SW.MessageBlock = function MessageBlock({ block, onSave }) {
    switch (block.type) {
      case 'text':
        return h('div', { className: 'sw-msg-text' }, SW.util.markdown(block.value));
      case 'code':
        return h(CodeBlock, { code: block.value, language: block.language });
      case 'sandbox_run':
        return h(SandboxRun, { block });
      case 'chart':
        return h(ChartBlock, {
          chartId: block.chartId,
          onAddToPlan: SW.store.get().activePlanId
            ? () => antd.message.success('Added to the plan as supporting evidence')
            : null,
        });
      case 'image':
        return h(ImageBlock, { block });
      case 'file':
        return h(FileCard, { block });
      case 'table':
        return h(TableBlock, { block });
      case 'choice':
        return h(SW.ChoiceCard, {
          prompt: block.prompt,
          options: block.options,
          onChoose: (option) => SW.store.chooseOption(option),
        });
      case 'resource_result':
        return h(SW.ResourceResultCard, {
          resourceId: block.resourceId,
          reason: block.reason,
          alternatives: block.alternatives,
        });
      case 'app_change':
        return h(AppChange, { block });
      case 'plan_card':
        return h(SW.PlanCard, { planId: block.planId });
      case 'plan_suggestion':
        return h(PlanSuggestion, null);
      case 'graduation_nudge':
        return h(GraduationNudge, { onSave });
      default:
        return null;
    }
  };

  // Pinning has to land somewhere real. A plan takes it; an app that was never
  // written down keeps it as a decision instead; a conversation about nothing in
  // particular has nowhere to put it, so it does not offer.
  function pinTargetFor(message) {
    const { activePlanId, activeApp, thread, touched } = SW.store.get();
    if (activePlanId) {
      return { title: 'Add to plan', onClick: () => antd.message.success('Added to the plan') };
    }
    // Chat has no preview to borrow a target from, so the conversation's own tag
    // stands in — but only while it points at one app. Two tags and the question
    // "a decision about what" has no answer this button can give.
    const tags = touched || [];
    const target =
      activeApp ||
      (tags.length === 1 ? { id: tags[0].appId, name: tags[0].appName } : null);
    if (!target) return null;
    const said = message.blocks
      .filter((b) => b.type === 'text')
      .map((b) => b.value.replace(/\*\*/g, ''))
      .join(' ')
      .trim();
    return {
      title: `Record as a decision on ${target.name}`,
      onClick: async () => {
        await SW.api.addDecision(target.id, {
          text: said.length > 160 ? `${said.slice(0, 160)}…` : said,
          conversationId: thread ? thread.id : null,
        });
        antd.message.success(`Recorded on ${target.name}. New conversations will read it.`);
      },
    };
  }

  SW.Message = function Message({ message, onSave }) {
    const { me } = SW.store.get();
    const isUser = message.role === 'user';
    const isSystem = message.role === 'system';
    const pinTarget = isUser ? null : pinTargetFor(message);

    if (isSystem) {
      return h(
        'div',
        { className: 'sw-msg' },
        h(
          'div',
          { className: 'sw-msg-body' },
          message.blocks.map((block, i) => h(SW.MessageBlock, { key: i, block, onSave }))
        )
      );
    }

    return h(
      'div',
      { className: `sw-msg ${isUser ? 'sw-msg-user' : 'sw-msg-assistant'}` },
      h(
        'div',
        { className: 'sw-msg-body' },
        h(
          'div',
          { className: 'sw-msg-who' },
          isUser
            ? h(SW.Avatar, { user: me, size: 20 })
            : h('span', { style: { fontSize: 14 } }, '✦'),
          isUser ? 'You' : 'Sage'
        ),

        (message.attachments || []).length > 0 &&
          h(
            'div',
            { className: 'sw-msg-attachments' },
            message.attachments.map((att) =>
              h(
                Tag,
                { key: att.resourceId, bordered: true, style: { display: 'inline-flex', gap: 6, alignItems: 'center' } },
                h('span', null, SW.util.iconFor(att.kind || 'file')),
                att.name
              )
            )
          ),

        h(
          'div',
          { className: 'sw-msg-blocks' },
          message.blocks.map((block, i) => h(SW.MessageBlock, { key: i, block, onSave }))
        ),

        !isUser &&
          h(
            'div',
            { className: 'sw-msg-actions' },
            h(
              Tooltip,
              { title: 'Copy' },
              h(Button, {
                type: 'text',
                size: 'small',
                icon: h(CopyOutlined, null),
                'aria-label': 'Copy message',
                onClick: () =>
                  SW.util.copy(
                    message.blocks.filter((b) => b.type === 'text').map((b) => b.value).join('\n\n')
                  ),
              })
            ),
            pinTarget &&
              h(
                Tooltip,
                { title: pinTarget.title },
                h(Button, {
                  type: 'text',
                  size: 'small',
                  icon: h(PushpinOutlined, null),
                  'aria-label': pinTarget.title,
                  onClick: pinTarget.onClick,
                })
              ),
            h(
              Tooltip,
              { title: 'Retry' },
              h(Button, {
                type: 'text',
                size: 'small',
                icon: h(ReloadOutlined, null),
                'aria-label': 'Retry',
                onClick: () => antd.message.info('Retry is not wired up in this prototype.'),
              })
            )
          )
      )
    );
  };

  SW.TypingIndicator = function TypingIndicator({ label }) {
    return h(
      'div',
      { className: 'sw-msg' },
      h(
        'div',
        { className: 'sw-msg-body' },
        h('div', { className: 'sw-msg-who' }, h('span', { style: { fontSize: 14 } }, '✦'), 'Sage'),
        h(
          'div',
          { className: 'sw-typing' },
          h('span', { className: 'sw-typing-dots' }, h('i'), h('i'), h('i')),
          h('span', null, label)
        )
      )
    );
  };
})();
