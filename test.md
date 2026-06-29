{                                                                                                                 
   "source": "datadog",                                                                                            
   "alert_type": "monitor_alert",                                                                                  
   "title": "CPU usage is high for host ip-10-0-12-34",                                                            
   "service": "host-infra",                                                                                        
   "severity": "critical",                                                                                         
   "timestamp": "2026-06-25T12:00:00Z",                                                                            
   "monitor": {                                                                                                    
     "id": 229292161,                                                                                              
     "name": "CPU usage is high for host {{host.name}}",                                                           
     "type": "query alert",                                                                                        
     "query": "avg(last_5m):100 - avg:system.cpu.idle{*} by {host} > 90",                                          
     "state": "Alert"                                                                                              
   },                                                                                                              
   "host": {                                                                                                       
     "name": "ip-10-0-12-34"                                                                                       
   },                                                                                                              
   "trigger": {                                                                                                    
     "metric": "system.cpu.idle",                                                                                  
     "derived_metric": "100 - system.cpu.idle",                                                                    
     "window": "last_5m",                                                                                          
     "threshold": 90,                                                                                              
     "observed_value": 96.4,                                                                                       
     "unit": "percent"                                                                                             
   },                                                                                                              
   "symptoms": [                                                                                                   
     "High CPU on host ip-10-0-12-34",                                                                             
     "Possible application slowdown",                                                                              
     "Potential timeouts under load"                                                                               
   ],                                                                                                              
   "raw_text": "Datadog monitor triggered: CPU usage is high for host ip-10-0-12-34. Query: avg(last_5m):100 -     
 avg:system.cpu.idle{*} by {host} > 90. Observed value 96.4% over last 5m."                                        
 }         