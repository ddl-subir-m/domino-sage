window.SW = window.SW || {};

(function () {
  const { createElement: h, useState } = React;
  const { Button, Space } = antd;
  const { CheckCircleFilled } = icons;

  const LEAD_OPTIONS = new Set(['find', 'delegate', 'auto']);

  // One component for both "should I find this for you?" and "should I add
  // this for you?" — the shapes are identical and the pattern should be too.
  SW.ChoiceCard = function ChoiceCard({ prompt, options, onChoose, disabled }) {
    const [chosen, setChosen] = useState(null);

    const pick = (option) => {
      if (chosen || disabled) return;
      setChosen(option);
      onChoose(option);
    };

    return h(
      'div',
      { className: 'sw-choice' },
      h('div', { className: 'sw-choice-prompt' }, SW.util.inline(prompt)),
      h(
        Space,
        { size: 8, wrap: true },
        options.map((option) =>
          h(
            Button,
            {
              key: option.id,
              size: 'small',
              type: LEAD_OPTIONS.has(option.id) ? 'primary' : 'default',
              disabled: Boolean(chosen) || disabled,
              onClick: () => pick(option),
            },
            option.label
          )
        )
      ),
      chosen &&
        h(
          'div',
          { className: 'sw-choice-resolved' },
          h(CheckCircleFilled, null),
          chosen.opensPanel
            ? 'Opened the Domino catalogue — add one there and I\'ll pick the thread back up.'
            : `You chose "${chosen.label}".`
        )
    );
  };

  SW.ResourceResultCard = function ResourceResultCard({ resourceId, reason, alternatives }) {
    const { resourceIndex } = SW.store.get();
    const resource = resourceIndex[resourceId] || { name: resourceId, kind: 'dataset' };
    const [replaced, setReplaced] = useState(false);

    return h(
      'div',
      { className: 'sw-result-card' },
      h(
        'div',
        { className: 'sw-result-head' },
        h('span', { className: 'sw-res-icon' }, SW.util.iconFor(resource.kind)),
        h('span', { className: 'sw-result-name' }, resource.name),
        h(SW.ProvenanceBadge, { addedBy: 'sage', rationale: reason })
      ),
      h('div', { className: 'sw-result-reason' }, reason),
      h(
        'div',
        { className: 'sw-result-actions' },
        replaced
          ? h('span', { className: 'sw-caption' }, 'Pick a different one from the catalogue.')
          : h(
              React.Fragment,
              null,
              h(
                Button,
                {
                  size: 'small',
                  type: 'primary',
                  onClick: () => {
                    SW.store.openDock('resources');
                    antd.message.success(`${resource.name} is in this app`);
                  },
                },
                'Use this'
              ),
              h(
                Button,
                {
                  size: 'small',
                  onClick: () => {
                    setReplaced(true);
                    SW.store.focusPanel(resource.kind);
                  },
                },
                'Show me others'
              ),
              (alternatives || []).length > 0 &&
                h(
                  'span',
                  { className: 'sw-caption' },
                  `Also considered: ${alternatives.join(', ')}`
                )
            )
      )
    );
  };
})();
