import json
import unittest
from unittest.mock import patch
import tracker

class TrackerTests(unittest.TestCase):
    def setUp(self):
        self.listing = {'id':'test','name':'Zelda controller','store':'Walmart','url':'https://www.walmart.com/ip/test','match':['zelda','40th','controller'],'exclude':['display stand'],'sellers':['Walmart'],'max_price':'99.99'}
        self.offer = {'price':'99.99','priceCurrency':'USD','seller':{'name':'Walmart'},'availability':'https://schema.org/InStock','itemCondition':'https://schema.org/NewCondition'}
    def parse(self, name='Zelda 40th controller'):
        data={'@type':'Product','name':name,'offers':self.offer}
        return tracker.inspect('<script type="application/ld+json">'+json.dumps(data)+'</script>',self.listing)
    def test_filters(self):
        self.assertEqual(self.parse()['status'],'eligible')
        self.offer['seller']['name']='Marketplace seller'
        self.assertEqual(self.parse()['status'],'unknown')
        self.offer['seller']['name']='Walmart'
        self.offer['price']='199.99'
        self.assertEqual(self.parse()['status'],'unavailable')
        self.offer['price']='NaN'
        self.assertEqual(self.parse()['status'],'unknown')
    def test_variant(self):
        self.assertEqual(self.parse('Zelda 40th controller with display stand')['status'],'unknown')
    def test_unknown_preserves_alert_and_restock_rearms(self):
        db=tracker.database(':memory:')
        yes={'status':'eligible','price':'99.99'}
        with patch('tracker.notify') as notify:
            for result in [yes,{'status':'unknown'},yes]:
                tracker.record(db,self.listing,result,True)
            self.assertEqual(notify.call_count,1)
            tracker.record(db,self.listing,{'status':'unavailable'},True)
            tracker.record(db,self.listing,yes,True)
            self.assertEqual(notify.call_count,2)
        db.close()
    def test_failed_delivery_retries(self):
        db=tracker.database(':memory:')
        with patch('tracker.notify',side_effect=[OSError(),None]) as notify:
            for _ in range(2):
                tracker.record(db,self.listing,{'status':'eligible','price':'99.99'},True)
            self.assertEqual(notify.call_count,2)
        db.close()

if __name__=='__main__': unittest.main()
