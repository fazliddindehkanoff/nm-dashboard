import re

def fix_file(filepath):
    with open(filepath, 'r') as f:
        content = f.read()

    # Red
    content = content.replace('style="background: rgba(220,38,38,0.12); color:#dc2626;"', 'class="bg-red-100 text-red-600 dark:bg-red-500/20 dark:text-red-400"')
    
    # Check if there are any remaining styles
    content = re.sub(
        r'<span class="flex items-center justify-center w-12 h-12 rounded-default shrink-0" class="([^"]+)">',
        r'<span class="flex items-center justify-center w-12 h-12 rounded-full shrink-0 \1">',
        content
    )

    with open(filepath, 'w') as f:
        f.write(content)

fix_file('templates/admin/index.html')
print("Done")
