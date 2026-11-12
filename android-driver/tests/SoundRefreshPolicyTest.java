import sa.kps.driver.SoundRefreshPolicy;
import java.util.Set;

public final class SoundRefreshPolicyTest {
    private static int checks;
    private static void check(boolean result){checks++;if(!result)throw new AssertionError("case "+checks);}
    public static void main(String[] args){
        SoundRefreshPolicy policy=new SoundRefreshPolicy();
        check(policy.begin("kps",0));
        check(!policy.begin("kps",59999));
        check(policy.begin("kps",60000));
        policy.unavailable("kps",60000,404);
        check(!policy.begin("kps",120000));
        check(policy.begin("enbishan",120000));
        check(!policy.begin("kps",60000+SoundRefreshPolicy.UNSUPPORTED_RETRY_MS-1));
        check(policy.begin("kps",60000+SoundRefreshPolicy.UNSUPPORTED_RETRY_MS));
        // A successful retry resumes ordinary polling, allowing future remote tone changes.
        check(policy.begin("kps",120000+SoundRefreshPolicy.UNSUPPORTED_RETRY_MS));
        policy.unavailable("enbishan",120000,501);
        check(!policy.begin("enbishan",180000));
        check(policy.begin("enbishan",120000+SoundRefreshPolicy.UNSUPPORTED_RETRY_MS));
        for(int status:new int[]{400,401,403,408,409,429,500,502,503,504}){
            SoundRefreshPolicy transientPolicy=new SoundRefreshPolicy();
            check(transientPolicy.begin("kps",0));
            transientPolicy.unavailable("kps",0,status);
            check(!transientPolicy.begin("kps",59999));
            check(transientPolicy.begin("kps",60000));
        }
        // Removed authorization scopes do not retain cache state on re-authorization.
        policy.retain(Set.of("enbishan"));
        check(policy.begin("kps",0));
        System.out.println("SoundRefreshPolicy: "+checks+" PASS");
    }
}
