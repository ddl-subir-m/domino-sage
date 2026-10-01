window.SW = window.SW || {};

(function () {
  const { createElement: h, useEffect, useRef, useState } = React;

  // A transcript that follows a running turn — but only for a reader who is at the bottom. Every
  // narration line and tool step of a turn changes what is drawn under the last message, and
  // following each of those unconditionally pulled a reader who had scrolled up back down every
  // time the reasoning moved, so nothing earlier could be read while a turn ran.
  //
  // `resetKey` names the conversation: a new one opens at its newest turn, whatever the last one
  // was scrolled to. `grew` is whatever changes when the transcript does. `behind` says something
  // arrived below a reader who had scrolled away, which is what the Jump to latest button is for.
  SW.useFollowLatest = function useFollowLatest(resetKey, grew) {
    const ref = useRef(null);
    // Kept by the scroll event rather than measured when new content lands: by then a tall card
    // has already pushed the bottom away, and a reader who was following would read as one who
    // had scrolled up.
    const atBottom = useRef(true);
    const [behind, setBehind] = useState(false);

    const toBottom = () => {
      const el = ref.current;
      if (el) el.scrollTop = el.scrollHeight;
    };
    const onScroll = () => {
      const el = ref.current;
      if (!el) return;
      atBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120;
      if (atBottom.current) setBehind(false);
    };
    // Something the reader did themselves — sending, or the button — puts them back at the end.
    const follow = () => {
      atBottom.current = true;
      setBehind(false);
    };
    const jump = () => {
      follow();
      toBottom();
    };

    useEffect(follow, [resetKey]);
    useEffect(() => {
      if (atBottom.current) toBottom();
      else setBehind(true);
    }, [resetKey, ...grew]);

    return { ref, onScroll, behind, follow, jump };
  };

  // Sticky at the foot of the scroller it is drawn in, so it needs no positioned wrapper.
  SW.JumpToLatest = function JumpToLatest({ onClick }) {
    const { Button } = antd;
    const { ArrowDownOutlined } = icons;
    return h(
      'div',
      { className: 'sw-jump-latest' },
      h(Button, { size: 'small', shape: 'round', icon: h(ArrowDownOutlined), onClick },
        'Jump to latest')
    );
  };
})();
