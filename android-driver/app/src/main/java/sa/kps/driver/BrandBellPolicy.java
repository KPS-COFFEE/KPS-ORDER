package sa.kps.driver;

import java.util.*;

/** Task brand, not login/home brand, owns the reminder's escalation clock. */
public final class BrandBellPolicy {
    private final Map<String,BellPolicy> brands=new TreeMap<>();
    public void update(Map<String,Set<String>> pending,long now,int first,int second,int continuous){
        brands.keySet().retainAll(pending.keySet());
        for(Map.Entry<String,Set<String>> entry:pending.entrySet()){
            BellPolicy bell=brands.computeIfAbsent(entry.getKey(),key->new BellPolicy());
            bell.configure(first,second,continuous);bell.update(entry.getValue(),now);
        }
    }
    public Set<String> active(){
        Set<String> active=new HashSet<>();
        for(Map.Entry<String,BellPolicy> entry:brands.entrySet())if(entry.getValue().count()>0)active.add(entry.getKey());
        return active;
    }
    public List<String> due(long now){
        List<String> result=new ArrayList<>();
        for(Map.Entry<String,BellPolicy> entry:brands.entrySet())if(entry.getValue().ring(now))result.add(entry.getKey());
        return result;
    }
    public int count(){int count=0;for(BellPolicy bell:brands.values())count+=bell.count();return count;}
    public void invalidate(){for(BellPolicy bell:brands.values())bell.invalidate();}
    public void clear(){brands.clear();}
}
