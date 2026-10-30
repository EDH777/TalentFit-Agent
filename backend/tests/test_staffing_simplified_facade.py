def test_recommendation_facade_starts_existing_agent_run(client, hr):
    created = client.post('/api/staffing/projects', headers=hr, json={
        'title': '简化入口项目', 'description': '测试推荐入口',
        'start_date': '2027-01-04', 'end_date': '2027-01-05',
    })
    assert created.status_code == 200
    project = created.json()
    response = client.post(
        f"/api/staffing/projects/{project['id']}/recommendation",
        headers=hr,
        json={'expected_version': project['version'], 'requirements_revision': project['requirements_revision']},
    )
    assert response.status_code == 422
    assert response.json()['detail']['code'] == 'REQUIREMENTS_UNCONFIRMED'


def test_recommendation_facade_keeps_admin_out_of_generation(client, hr, admin):
    created = client.post('/api/staffing/projects', headers=hr, json={
        'title': '权限边界项目', 'description': '测试权限',
        'start_date': '2027-01-04', 'end_date': '2027-01-05',
    })
    assert created.status_code == 200
    project = created.json()
    response = client.post(
        f"/api/staffing/projects/{project['id']}/recommendation",
        headers=admin,
        json={'expected_version': project['version'], 'requirements_revision': project['requirements_revision']},
    )
    assert response.status_code == 403
