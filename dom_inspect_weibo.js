() => {
    const results = [];

    const items = document.querySelectorAll('.wbpro-scroller-item');
    results.push('Items: ' + items.length);

    for (let i = 0; i < items.length; i++) {
        const item = items[i];
        const textDiv = item.querySelector('.text');
        results.push('\nItem ' + i + ':');
        if (textDiv) {
            results.push('  .text innerHTML: ' + textDiv.innerHTML.substring(0, 500));
            results.push('  .text innerText: ' + textDiv.innerText.substring(0, 200));

            // Check all child elements
            const children = textDiv.children;
            for (let j = 0; j < children.length; j++) {
                const child = children[j];
                const cls = typeof child.className === 'string' ? child.className : '';
                results.push('  child ' + j + ': tag=' + child.tagName + ' class=' + cls + ' text=' + child.innerText.substring(0, 100));
            }

            // Check all links
            const links = textDiv.querySelectorAll('a');
            for (const link of links) {
                results.push('  link: href=' + link.href + ' text=' + link.innerText);
            }

            // Check all spans
            const spans = textDiv.querySelectorAll('span');
            for (const span of spans) {
                const cls = typeof span.className === 'string' ? span.className : '';
                results.push('  span: class=' + cls + ' text=' + span.innerText.substring(0, 100));
            }
        } else {
            results.push('  No .text div found');
            results.push('  HTML: ' + item.innerHTML.substring(0, 500));
        }
    }

    return results.join('\n');
}
