() => {
    const results = [];

    // Click all expand buttons and collect info
    const expandBtns = document.querySelectorAll('button.comment-reply-expand-btn');
    results.push('Expand buttons found: ' + expandBtns.length);
    for (const btn of expandBtns) {
        results.push('  Button: ' + (btn.innerText || '').trim());
    }

    // Check for reply items - look for nested comment structures
    results.push('\n--- Looking for reply/nested comment structure ---');

    // After expanding, replies might have different structure
    // Check all comment items
    const commentItems = document.querySelectorAll('[data-e2e="comment-item"]');
    results.push('Total comment items: ' + commentItems.length);

    for (let i = 0; i < Math.min(commentItems.length, 5); i++) {
        const item = commentItems[i];
        const text = (item.innerText || '').substring(0, 150);
        const cls = typeof item.className === 'string' ? item.className : '';

        // Check if this item contains nested comment items
        const nestedItems = item.querySelectorAll('[data-e2e="comment-item"]');
        const hasNested = nestedItems.length > 0;

        // Check for reply-specific classes
        const replyEls = item.querySelectorAll('[class*="reply"], [class*="Reply"]');

        results.push('\nItem ' + i + ': class=' + cls.substring(0, 80));
        results.push('  text: ' + text);
        results.push('  nested comment-items: ' + nestedItems.length);
        results.push('  reply elements: ' + replyEls.length);

        // Check for author indicator
        const authorBadge = item.querySelector('[class*="author"], [class*="Author"]');
        if (authorBadge) {
            results.push('  has author badge');
        }
    }

    return results.join('\n');
}
