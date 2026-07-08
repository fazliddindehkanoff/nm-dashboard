import re

def fix_file(filepath):
    with open(filepath, 'r') as f:
        content = f.read()

    # Replace inline styles with tailwind classes
    # Purple
    content = content.replace('style="background: rgba(147,51,234,0.12); color:#9333ea;"', 'class="bg-purple-100 text-purple-600 dark:bg-purple-500/20 dark:text-purple-400"')
    # Orange
    content = content.replace('style="background: rgba(234,88,12,0.12); color:#ea580c;"', 'class="bg-orange-100 text-orange-600 dark:bg-orange-500/20 dark:text-orange-400"')
    # Emerald/Green
    content = content.replace('style="background: rgba(16,185,129,0.12); color:#10b981;"', 'class="bg-emerald-100 text-emerald-600 dark:bg-emerald-500/20 dark:text-emerald-400"')
    # Blue
    content = content.replace('style="background: rgba(59,130,246,0.12); color:#3b82f6;"', 'class="bg-blue-100 text-blue-600 dark:bg-blue-500/20 dark:text-blue-400"')
    # Pink
    content = content.replace('style="background: rgba(219,39,119,0.12); color:#db2777;"', 'class="bg-pink-100 text-pink-600 dark:bg-pink-500/20 dark:text-pink-400"')

    # Replace the wrapping span to include these classes and use rounded-full
    content = re.sub(
        r'<span class="flex items-center justify-center w-12 h-12 rounded-default shrink-0" class="([^"]+)">',
        r'<span class="flex items-center justify-center w-12 h-12 rounded-full shrink-0 \1">',
        content
    )

    with open(filepath, 'w') as f:
        f.write(content)

fix_file('templates/admin/index.html')
fix_file('templates/admin/salaries.html')
print("Done")
