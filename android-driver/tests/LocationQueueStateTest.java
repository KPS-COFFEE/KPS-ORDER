import sa.kps.driver.LocationQueueState;
import sa.kps.driver.LocationQueuePolicy;
import java.util.*;
public final class LocationQueueStateTest {
    public static void main(String[] args) {
        long now=1800000000000L;
        LocationQueueState q=new LocationQueueState(0);
        for(int i=0;i<120;i++)q.append(point("p"+i,now),now);
        check(q.size()==120);
        List<LocationQueueState.Point> first=q.batch(now);
        check(first.size()==100);check(first.get(99).id.equals("p119"));
        // Lost HTTP response keeps the entire exact batch for idempotent replay.
        List<LocationQueueState.Point> replay=q.batch(now);
        for(int i=0;i<100;i++){check(first.get(i).id.equals(replay.get(i).id));check(first.get(i).capturedAt==replay.get(i).capturedAt);}
        q.append(point("p0",now),now);check(q.size()==120);
        q.acknowledge(new HashSet<>(Arrays.asList("p0","p4","unknown")));check(q.size()==118);
        q.acknowledge(new HashSet<>(Arrays.asList("p0","p4")));check(q.size()==118);
        check(q.batch(now).get(0).id.equals("p1"));
        q.append(point("p120",now),now);check(q.batch(now).get(99).id.equals("p120"));
        LocationQueueState recovered=new LocationQueueState(q.dropped());
        for(LocationQueueState.Point p:q.snapshot())recovered.append(p,now);
        check(recovered.size()==119);check(recovered.batch(now).get(0).id.equals("p1"));
        // Only acknowledged IDs disappear; unacknowledged samples survive recovery.
        q.prune(now+LocationQueuePolicy.MAX_AGE_MS+1);check(q.size()==0);check(q.dropped()==119);
        q=new LocationQueueState(0);
        for(int i=0;i<LocationQueuePolicy.MAX_POINTS+1;i++)q.append(point("n"+i,now),now);
        check(q.size()==LocationQueuePolicy.MAX_POINTS);check(q.dropped()==1);check(q.batch(now).get(0).id.equals("n1"));
        q.append(point("future",now+30001),now);check(q.dropped()==2);
        System.out.println("Queue replay/partial+duplicate acknowledgement/recovery/retention/overflow PASS");
    }
    static LocationQueueState.Point point(String id,long time){return new LocationQueueState.Point(id,time,28.4,45.9,10);}
    static void check(boolean value){if(!value)throw new AssertionError();}
}
