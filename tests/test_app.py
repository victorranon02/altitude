import unittest

from openpyxl import Workbook

from app import app, db, Piloto, OrdemServico, ExecucaoOS


class AltitudeAppTests(unittest.TestCase):
    def setUp(self):
        app.config.update(
            TESTING=True,
            SQLALCHEMY_DATABASE_URI='sqlite:///:memory:',
            WTF_CSRF_ENABLED=False,
        )
        self.client = app.test_client()
        with app.app_context():
            db.drop_all()
            db.create_all()

    def tearDown(self):
        with app.app_context():
            db.session.remove()
            db.drop_all()

    def test_home_page_loads(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)

    def test_admin_reports_page_loads(self):
        with app.app_context():
            db.session.add(Piloto(nome='Admin Teste', usuario='admin_teste', senha='123'))
            db.session.commit()
            piloto_id = Piloto.query.filter_by(usuario='admin_teste').first().id

        with self.client.session_transaction() as sess:
            sess['piloto_id'] = piloto_id

        response = self.client.get('/admin/relatorios')
        self.assertEqual(response.status_code, 200)

    def test_import_keeps_unidade_column(self):
        wb = Workbook()
        ws = wb.active
        ws.title = 'OS'
        ws.append(['OS', 'Operação', 'Data', 'Fazenda', 'Setor', 'Área', 'Unidade'])
        ws.append(['OS-1', 'Plantio', '01/01/2026', 'Fazenda A', 'Setor 1', 10.5, 'UNIDADE 1'])

        with app.app_context():
            db.session.add(Piloto(nome='Admin Teste', usuario='admin_teste', senha='123', perfil='ADMINISTRADOR'))
            db.session.commit()
            piloto_id = Piloto.query.filter_by(usuario='admin_teste').first().id

        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess['piloto_id'] = piloto_id
                sess['perfil'] = 'ADMINISTRADOR'

            with open('tests/temp_unidade.xlsx', 'wb') as f:
                wb.save(f)

            with open('tests/temp_unidade.xlsx', 'rb') as f:
                response = client.post('/admin', data={'arquivo': (f, 'temp_unidade.xlsx')}, content_type='multipart/form-data')

        self.assertEqual(response.status_code, 200)

        with app.app_context():
            os_item = OrdemServico.query.filter_by(os='OS-1').first()
            self.assertIsNotNone(os_item)
            self.assertEqual(os_item.unidade, 'UNIDADE 1')

    def test_excel_export_route_works(self):
        with app.app_context():
            db.session.add(Piloto(nome='Admin Teste', usuario='admin_teste', senha='123'))
            db.session.commit()
            piloto_id = Piloto.query.filter_by(usuario='admin_teste').first().id

        with self.client.session_transaction() as sess:
            sess['piloto_id'] = piloto_id

        response = self.client.get('/admin/exportar_excel')
        self.assertEqual(response.status_code, 200)
        self.assertIn('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', response.content_type)

    def test_pilot_status_is_shown_and_os_can_be_finalized(self):
        os_id = None
        piloto_id = None
        with app.app_context():
            piloto = Piloto(nome='Piloto Teste', usuario='piloto_teste', senha='123', perfil='PILOTO')
            db.session.add(piloto)
            db.session.flush()
            piloto_id = piloto.id

            os_item = OrdemServico(os='OS-STATUS', operacao='Plantio', fazenda='Fazenda X', setor='Setor 1', area_os=10.0)
            db.session.add(os_item)
            db.session.flush()
            os_id = os_item.id

            db.session.add(ExecucaoOS(os_id=os_id, piloto_id=piloto_id, auxiliar='Aux 1', area=4.0, observacao='Parcial'))
            db.session.commit()

        with self.client.session_transaction() as sess:
            sess['piloto_id'] = piloto_id
            sess['perfil'] = 'PILOTO'

        response = self.client.get('/piloto')
        self.assertEqual(response.status_code, 200)
        self.assertIn('EM ANDAMENTO', response.text)

        with app.app_context():
            os_item = OrdemServico.query.filter_by(os='OS-STATUS').first()
            self.assertFalse(os_item.finalizado)

        response = self.client.post(f'/os/{os_id}', data={'auxiliar': 'Aux 1', 'area': '6.0', 'observacao': 'Finalizando', 'finalizar_os': 'on'})
        self.assertEqual(response.status_code, 302)

        with app.app_context():
            os_item = OrdemServico.query.filter_by(os='OS-STATUS').first()
            self.assertTrue(os_item.finalizado)
            self.assertEqual(os_item.status_label(), 'FINALIZADO')


if __name__ == '__main__':
    unittest.main()
