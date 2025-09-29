import json
import re



def generate_latex_content(song):
    def safe_escape(value, default='Neznámy'):
        if value is None:
            return default
        if isinstance(value, str) and value.strip() == '':
            return default
        return escape_latex(value)

    def escape_latex(text):
        if not text:
            return ""
        # First protect chord content before general escaping
        protected = []
        for segment in re.split(r'(\[[^\]]+\])', text):
            if segment.startswith('[') and segment.endswith(']'):
                # Chord content - escape backslashes and special LaTeX chars including #
                chord_content = segment[1:-1]
                # Escape LaTeX special chars inside chord:
                chord_content = (chord_content
                    .replace('\\', '\\textbackslash ')
                    .replace('&', '\\&')
                    .replace('%', '\\%')
                    .replace('$', '\\$')
                    .replace('#', '\\#')
                    .replace('_', '\\_')
                    .replace('{', '\\{')
                    .replace('}', '\\}')
                    .replace('~', '\\textasciitilde{}')
                    .replace('^', '\\textasciicircum{}')
                )
                protected.append(f'[{chord_content}]')
            else:
                # Normal text - full escaping
                protected.append(
                    segment.replace('&', '\\&').replace('%', '\\%')
                        .replace('$', '\\$').replace('#', '\\#')
                        .replace('_', '\\_').replace('{', '\\{')
                        .replace('}', '\\}').replace('~', '\\textasciitilde{}')
                        .replace('^', '\\textasciicircum{}')
                        .replace('\\', '\\textbackslash{}')
                )
        return ''.join(protected)

    def convert_chords(line):
        # First properly escape the entire line (handling chords specially)
        escaped_line = escape_latex(line)
        # Now convert chord markers to LaTeX commands
        return re.sub(r'\[([^\]]+)\]', r'\\chord{\1}', escaped_line)

    def format_block(block):
        formatted_lines = [convert_chords(line) for line in block['lines']]
        content = ' \\linebreak \n '.join(formatted_lines)  # Use \\ for line breaks

        # Properly escape the block type and format
        block_type = block['type'].lower()
        return f'\\{block_type}block{{\n{content}\n}}'

    # Load and process song parts
    parts = json.loads(song.song_parts)
    formatted_parts = '\n\n'.join(format_block(part) for part in parts)

    # Prepare categories if they exist
    categories = escape_latex(song.categories) if hasattr(song, 'categories') and song.categories else ""

    return f"""\\documentclass[11pt]{{article}}
\\input{{preamble.tex}}

% ----------------- Song Metadata -----------------
\\newcommand{{\\songID}}{{{safe_escape(getattr(song, 'song_id', None), '')}}}
\\newcommand{{\\songName}}{{{safe_escape(getattr(song, 'title', None))}}}
\\newcommand{{\\origSongName}}{{{safe_escape(getattr(song, 'title_original', None))}}}
\\newcommand{{\\categories}}{{{categories.replace(';;', ', ')}}}
\\newcommand{{\\artistName}}{{{safe_escape(getattr(song, 'author', None))}}}
\\newcommand{{\\origArtistName}}{{{safe_escape(getattr(song, 'author_original', None))}}}
\\newcommand{{\\versionName}}{{{safe_escape(getattr(song, 'version_name', None), '')}}}
\\setboolean{{showchords}}{{True}}
\\begin{{document}}

\\noindent
\\begin{{minipage}}[t]{{0.8\\textwidth}}
    % Main title
    \\begin{{minipage}}[t]{{\\textwidth}}
        \\raggedright % Ensures left alignment without stretching
        \songTitleStyle{{\songName}}%
    \\end{{minipage}}

    % Artist line
    \\artistLine

    % Categories
    \\categoryTags

    % Divider line
    \\vspace{{3pt}}
    \\tikz{{\\draw[gray!40, line width=1.7pt] (0,0) -- (\\linewidth,0);}}
\\end{{minipage}}%
\\hfill
\\songHeaderBox
\\vspace{{5pt}}

\\begin{{spacing}}{{1.3}}

{formatted_parts}

\\end{{spacing}}

\\end{{document}}"""