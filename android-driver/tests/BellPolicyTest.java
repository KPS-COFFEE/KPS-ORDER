import java.util.*;
import sa.kps.driver.BellPolicy;

public final class BellPolicyTest {
    private static int checks;
    private static void check(boolean result){checks++;if(!result)throw new AssertionError("case "+checks);}
    public static void main(String[] args){
        check(BellPolicy.pending("assigned","one","one",""));
        check(!BellPolicy.pending("accepted","one","one",""));
        check(BellPolicy.pending("picked_up","other","one","incoming-shift"));
        check(!BellPolicy.pending("assigned","other","one",""));
        check(!BellPolicy.pending("assigned","other","one","null"));
        for(String terminal:new String[]{"delivered","cancelled","transferred","reassigned","unknown",""})
            check(!BellPolicy.pending(terminal,"other","one","incoming-shift"));
        check(!BellPolicy.pending("assigned","","one","incoming-shift"));
        BellPolicy bell=new BellPolicy();Set<String> pending=new HashSet<>(Arrays.asList("task-one"));
        bell.update(pending,0);check(bell.ring(0));check(!bell.ring(1000));
        // Opening/reading is not an API state change: the same pending task still rings.
        bell.update(pending,30000);check(bell.ring(30000));
        bell.update(pending,60000);check(bell.ring(60000));
        bell.update(pending,90000);check(bell.ring(90000));
        check(!bell.ring(121001)); // no fresh verification, no stale alert
        bell.invalidate();check(!bell.ring(125000));
        bell.update(pending,130000);check(bell.ring(130000));
        bell.update(Collections.emptySet(),160000);check(bell.count()==0);check(!bell.ring(160000));
        bell.update(pending,170000);check(bell.ring(170000));
        bell.clear();check(bell.count()==0);check(!bell.ring(200000));
        bell.configure(10,40,70);bell.update(pending,210000);check(!bell.ring(210000));check(bell.ring(220000));
        bell.update(pending,250000);check(bell.ring(250000));
        bell.clear();check(!bell.ring(0));
        System.out.println(checks+" mandatory bell lifecycle cases PASS");
    }
}
