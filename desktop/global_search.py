"""Read-only workspace search over one explicitly refreshed snapshot."""
import json
from desktop.task_search import normalized,match_details,match_excerpt


def search(tasks,plans,query='',*,scope='title',status='全部状态',start=None,end=None,plan_id=None,include_archived=False,entity='all',resources=(),features=False):
    from desktop.task_search import search_tasks
    rows=[]
    if entity in ('all','task'):
        eligible=[r for r in tasks if (include_archived or not r['archived']) and (not plan_id or (r['plan_id'] is None if plan_id=='__standalone__' else r['plan_id']==plan_id))]
        for r in search_tasks(eligible,query,status,start,end,scope=scope):
            matches=match_details(r,query,scope)
            why='；'.join(f'{label}：{match_excerpt(value,terms,48)}' for label,value,terms in matches)
            rows.append(dict(kind='task',id=r['id'],title=r['task'].title,plan=r['plan_title'],date=r['due_date'],status=r['status'],match=why))
    if entity in ('all','plan') and status=='全部状态' and plan_id!='__standalone__':
        terms=normalized(query).split()
        for p in plans:
            if (p['archived'] and not include_archived) or (plan_id and p['id']!=plan_id):continue
            if start and p['start_date']<start or end and p['start_date']>end:continue
            profile=json.loads(p['profile_json'])
            fields=[('计划名称',p['title'])]
            if scope=='all':fields += [('概述',p['overview']),('科目',profile.get('subject','')),('目标',profile.get('goal',''))]
            if not all(any(t in normalized(value) for _,value in fields) for t in terms):continue
            why='；'.join(f'{label}：{match_excerpt(value,[t for t in terms if t in normalized(value)],48)}' for label,value in fields if any(t in normalized(value) for t in terms))
            rows.append(dict(kind='plan',id=p['id'],title='计划 · '+p['title'],plan='计划本身',date=p['start_date'],status='已归档' if p['archived'] else '计划',match=why))
    if entity in ('all','resource'):
        from pathlib import PureWindowsPath
        eligible={r['id']:r for r in tasks if (include_archived or not r['archived']) and
                  (not plan_id or (r['plan_id'] is None if plan_id=='__standalone__' else r['plan_id']==plan_id))}
        eligible={r['id']:r for r in search_tasks(list(eligible.values()),'',status,start,end,scope='title')}
        terms=normalized(query).split()
        for resource in resources:
            r=eligible.get(resource['task_id'])
            if not r:continue
            name=resource['title'];target=resource['target']
            fields=[('关联名称',name)]
            if resource['kind']=='file':fields.append(('文件名',PureWindowsPath(target).name))
            if scope=='all':fields.extend([('文件路径' if resource['kind']=='file' else '网址',target),('所属任务',r['task'].title),('所属计划',r['plan_title'])])
            if not all(any(t in normalized(value) for _,value in fields) for t in terms):continue
            why='；'.join(f'{label}：{match_excerpt(value,[t for t in terms if t in normalized(value)],48)}' for label,value in fields if any(t in normalized(value) for t in terms))
            rows.append(dict(kind='resource',id=json.dumps([r['id'],resource['id']]),task_id=r['id'],resource_id=resource['id'],
                title=name,plan=r['task'].title,date=r['due_date'],status=r['status'],match=why,role=resource['role']))
    if features and entity in ('all','feature'):
        from desktop.feature_search import FEATURES
        terms=normalized(query).split()
        for key,title,description,aliases in FEATURES:
            fields=[('功能名称',title),('功能关键词',aliases),('功能说明',description)]
            if not all(any(t in normalized(value) for _,value in fields) for t in terms):continue
            why='；'.join(f'{label}：{match_excerpt(value,[t for t in terms if t in normalized(value)],48)}' for label,value in fields if any(t in normalized(value) for t in terms))
            rows.append(dict(kind='feature',id=key,title=title,plan=description,date='—',status='功能入口',match=why))
    return rows
