() => {
    const results = [];
    const selectors = [
        '[class*="comment"]',
        '[class*="Comment"]',
        '[class*="reply"]',
        '[node-type="commentList"]',
    ];
    for (const sel of selectors) {
        try {
            const els = document.querySelectorAll(sel);
            if (els.length > 0) {
                results.push(sel + ': ' + els.length + ' elements');
                const first = els[0];
                results.push('  tag: ' + first.tagName + ', class: ' + first.className);
                results.push('  html: ' + first.outerHTML.substring(0, 500));
            }
        } catch(e) {}
    }
    const body = document.body.innerText;
    const lines = body.split('\n').filter(l => l.trim().length > 10);
    results.push('--- Page text ---');
    for (const line of lines.slice(0, 30)) {
        results.push(line.trim().substring(0, 100));
    }
    return results.join('\n');
}
