import re

def fix_file(filepath):
    with open(filepath, 'r') as f:
        content = f.read()

    neutral_class = 'class="bg-base-100 text-base-700 dark:bg-base-800 dark:text-base-300"'
    content = re.sub(
        r'style="background: rgba\([^)]+\); color:#[0-9a-fA-F]{6};"',
        neutral_class,
        content,
    )

    content = re.sub(
        r'<span class="flex items-center justify-center w-12 h-12 rounded-default shrink-0" class="([^"]+)">',
        r'<span class="flex items-center justify-center w-12 h-12 rounded-full shrink-0 \1">',
        content
    )

    with open(filepath, 'w') as f:
        f.write(content)

fix_file('templates/admin/index.html')
print("Done")
