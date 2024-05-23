#!/usr/bin/env python3
""" Utility script for grading student projects in repositories
"""

import os
import os.path as op
import shutil
from pathlib import Path
import re
from subprocess import run as prun
from argparse import ArgumentParser, RawDescriptionHelpFormatter

import yaml
import nbformat as nbf

import mcpmark.mcputils as mcu
from gradools.canvastools import to_minimal_df

GITIGNORE = """
.ipynb_checkpoints/
*.Rmd
__pycache__/
"""

MARK_CATEGORIES = ('Questions', 'Analysis', 'Results', 'Readability',
                   'Writing', 'Reproducibility')


def read_yaml(fname):
    with open(fname, 'rt') as fobj:
        config = yaml.load(fobj, Loader=yaml.SafeLoader)
    return config


def get_class_list(config):
    handler = mcu.make_submission_handler(config)
    df = handler.read_student_data()
    df['id'] = df.index
    df = df.set_index(config['student_id_col'],
                      drop=False)
    return df.drop(index=config.get('missing', []))


def check_config(config, write_missing=True):
    all_members = set()
    class_list = get_class_list(config)
    known_students = set(class_list.index)
    for name, pconfig in config.get('projects', {}).items():
        members = member_logins(pconfig)
        unknown = members.difference(known_students)
        if len(unknown):
            raise ValueError(f"Unknown students {', '.join(unknown)} in {name}")
        if len(all_members.intersection(members)):
            raise ValueError(f'Students in {name} overlap with other project')
        all_members.update(members)
    missing = known_students.difference(all_members)
    if not missing:
        print('No missing students')
        return
    mdf = class_list.loc[list(missing)]
    print('Missing students')
    print(mdf)
    if write_missing:
        mdf.to_csv('missing.csv', index=None)


def member_logins(pconfig):
    members = pconfig['members']
    return set(m.split('@')[0] for m in members)


def report(config):
    class_list = get_class_list(config)
    for name, pconfig in config.get('projects', {}).items():
        print(name)
        print('=' * len(name))
        members = member_logins(pconfig)
        print(class_list.loc[list(members)])
        print()


def _get_org_url(config):
    org_url = config.get('projects_url')
    if not org_url:
        raise RuntimeError('Set "projects_url" in config')
    return org_url


def make_repos(config, check=True):
    org_url = _get_org_url(config)
    # hub below depends on Github
    assert 'github.com' in org_url
    org_name = org_url.split('/')[-1]
    for name in config.get('projects', {}):
        if op.isdir(name):
            print(f'Existing repository "{name}"')
            continue
        print(f'Creating repository "{name}"')
        os.mkdir(name)
        prun(['git', 'init'], cwd=name, check=check)
        prun(['hub', 'create', f'{org_name}/{name}', '--private'],
            cwd=name, check=check)


def cmd_in_repos(config, cmd, check=True):
    for name in config.get('projects', {}):
        cmd_str = '\n'.join(cmd) if not isinstance(cmd, str) else cmd
        print(f'Running {cmd_str} in {name}')
        prun(cmd, cwd=name, check=check, shell=True)


def pull_repos(config, check=True, rebase=False, push=False):
    cmd = ['git', 'pull'] + (['--rebase'] if rebase else [])
    for name in config.get('projects', {}):
        print(f'Pull for {name}')
        prun(cmd, cwd=name, check=check)
        if push and rebase:
            prun(['git', 'push'], cwd=name, check=check)


def add_submodules(config, check=True):
    org_url = _get_org_url(config)
    for name in config.get('projects', {}):
        prun(['git', 'submodule', 'add', f'{org_url}/{name}',
             name], check=check)


def write_gitignore(config, check=True):
    for name in config.get('projects', {}):
        fname = op.join(name, '.gitignore')
        with open(fname, 'wt') as fobj:
            fobj.write(GITIGNORE)
        prun(['git', 'add', '.gitignore'], cwd=name, check=check)
        prun(['git', 'commit', '-m', 'Add .gitignore'],
            cwd=name, check=check)


def get_proj_marks(path):
    path = Path(path)
    nb_paths = path.glob('*.ipynb')
    for nb_path in nb_paths:
        marks = get_nb_marks(nb_path)
        if marks:
            return marks


def get_nb_marks(nb_path):
    nb = nbf.read(nb_path, nbf.NO_CONVERT)
    if len(nb.cells) == 0:
        return
    last_cell = nb.cells[-1]
    if last_cell['cell_type'] != 'markdown':
        return
    last_lines = last_cell['source'].splitlines()
    if len(last_lines) < 2 or last_lines.pop(0) != '## Marks':
        return
    marks = {}
    for L in last_lines[1:]:
        if (m := re.match(r'\*\s*(\w*)\s*:\s*([0-9.]+)\s*$', L)):
            k, v = m.groups()
            marks[k] = float(v)
    return marks


def get_marks(config, allow_missing=False):
    class_list = get_class_list(config)
    proj_col = config.get('marks_col', 'Project %')
    project_root = Path(config.get('projects_path', '.'))
    for name, pconfig in config.get('projects', {}).items():
        marks = {'Project name': name,
                 'Presentation': pconfig['presentation']}
        proj_marks = get_proj_marks(project_root / name)
        if proj_marks is None:
            msg = f'Missing project marks for {name}'
            if not allow_missing:
                raise RuntimeError(msg)
            print(msg)
            continue
        assert set(proj_marks) == set(MARK_CATEGORIES)
        marks.update(proj_marks)
        for member, contrib_score in pconfig['members'].items():
            marks['Contribution'] = contrib_score
            class_list.loc[member, list(marks)] = marks
    class_list[proj_col] = (
        class_list.loc[:, ('Presentation',) + MARK_CATEGORIES]
        .astype(float)
        .mean(axis=1))
    if config.get('round_final'):
        class_list[proj_col] = class_list[proj_col].round()
    return class_list


def write_marks(config, allow_missing=False):
    marks_froot = config.get('marks_froot', 'project_marks')
    marks_fname = f"{marks_froot}.csv"
    class_list = get_marks(config, allow_missing)
    class_list.to_csv(marks_fname, index=None)


def write_project_list(config):
    with_project_list(config).to_csv('project_list.csv', index=None)



def with_project_list(config):
    class_list = get_class_list(config)
    class_list['project'] = ''
    for name, pconfig in config.get('projects', {}).items():
        members = member_logins(pconfig)
        class_list.loc[list(members), 'project'] = name
    return class_list


def get_member2project(projects):
    recoder = {}
    for p, p_dict in projects.items():
        for m in p_dict['members']:
            recoder[m] = p
    return recoder


def write_feedback(config, out_path):
    class_list = get_marks(config)
    marks_col = config['marks_col']
    if config.get('round_final', False):
        class_list[marks_col] = class_list[marks_col].round()
    handler = mcu.make_submission_handler(config)
    m2proj = get_member2project(config['projects'])
    project_root = Path(config.get('projects_path', '.'))
    out_path = Path(out_path)
    for user, row in class_list.iterrows():
        if not (project := m2proj.get(user)):
            continue
        login = row[config['feedback_id_col']]
        jh_user = handler.login2jh(login)
        fb_in_path = project_root / project
        fb_out_path = out_path / jh_user / 'project'
        shutil.copytree(fb_in_path, fb_out_path)
        marks = row.loc['Presentation':]
        (fb_out_path / 'marks.md').write_text(
            marks.to_markdown())


def export_marks(config, allow_missing=False):
    mark_df = get_marks(config, allow_missing)
    if not (gb_fname := config.get('canvas_export_path')):
        raise RuntimeError('Set "canvas_export_path" in config')
    canvas_df = to_minimal_df(gb_fname)
    export_config = config.get('export', {})
    merge_col = export_config.get('merge_col')
    out_col_map = export_config.get('col_map')
    mark_df = (mark_df[[merge_col] + list(out_col_map)]
               .rename(columns=out_col_map))
    out_df = canvas_df.merge(mark_df, on=merge_col)
    out_df.to_csv(export_config.get('fname', 'export.csv'),
                  index=None)


def get_parser():
    parser = ArgumentParser(description=__doc__,  # Usage from docstring
                            formatter_class=RawDescriptionHelpFormatter)
    parser.add_argument('action',
                        help='One of ' + ','.join([f"'{a}'" for a in ACTIONS]))
    parser.add_argument('--config', default='projects.yaml',
                        help='YaML configuration for course')
    parser.add_argument('--repo-cmd',
                        help='Command to run in repository '
                        '(for repo-in-cmd action')
    parser.add_argument('--no-check', action='store_true',
                        help='Disable error on failed shell commands')
    parser.add_argument('--rebase', action='store_true',
                        help='Rebase, push on git pull')
    parser.add_argument('--allow-missing', action='store_true',
                        help='Whether to allow missing marks without error')
    parser.add_argument('--feedback-out-path', default='feedback',
                        help='Path to write feedback files')
    return parser


def main():
    parser = get_parser()
    args = parser.parse_args()
    config = read_yaml(args.config)
    if args.action == 'check':
        check_config(config)
    elif args.action == 'report':
        report(config)
    elif args.action == 'make-repos':
        make_repos(config, not args.no_check)
    elif args.action == 'pull-repos':
        pull_repos(config, not args.no_check, args.rebase, args.rebase)
    elif args.action == 'add-submodules':
        add_submodules(config, not args.no_check)
    elif args.action == 'cmd-in-repos':
        if not args.repo_cmd:
            raise RuntimeError('Specify --repo-cmd')
        cmd_in_repos(config, args.repo_cmd, not args.no_check)
    elif args.action == 'write-gitignore':
        write_gitignore(config, not args.no_check)
    elif args.action == 'write-marks':
        write_marks(config, args.allow_missing)
    elif args.action == 'write-project-list':
        write_project_list(config)
    elif args.action == 'write-feedback':
        write_feedback(config, args.feedback_out_path)
    elif args.action == 'export-marks':
        export_marks(config, args.allow_missing)
    else:
        alist = "', '".join(ACTIONS)
        raise RuntimeError(f"action should be in '{alist}'")


ACTIONS = ('check',
           'report',
           'make-repos',
           'pull-repos',
           'add-submodules',
           'cmd-in-repos',
           'write-gitignore',
           'write-marks',
           'write-project-list',
           'write-feedback',
           'export-marks',
          )


if __name__ == '__main__':
    main()
